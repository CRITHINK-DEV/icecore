{#- Helpers shared by the UC2 generic tests (tests/generic/): the history reads that give a test its
    baseline (previous schema signature, start of the next change window), and bound validation and comparison.
    History reads go through icecore.base_events() and _icecore_safe_scalar, so a missing event table or a failed
    read means "no baseline" and never fails a test. -#}


{#- The event table the history reads use (the target database's EVENT_TABLE, mapped by event_table_to_read), or
    none when none is active or the lookup fails. resolve_event_table raises when there is none, and a Jinja macro
    cannot catch that, so the lookup runs in a Snowflake Scripting block that returns the error instead; the mapping
    is event_table_to_read's. -#}
{% macro _icecore_history_event_table() %}
  {%- set block -%}
declare
  v varchar default null;
  qid varchar;
begin
  show parameters like 'EVENT_TABLE' in database {{ icecore._icecore_identifier(target.database) }};
  qid := sqlid;
  select max("value") into :v from table(result_scan(:qid));
  return object_construct('value', v);
exception
  when other then return object_construct('error', sqlerrm);
end;
  {%- endset -%}
  {%- set out = fromjson(run_query('execute immediate ' ~ icecore._icecore_sql_literal(block)).rows[0][0]) -%}
  {%- if 'error' in out -%}
    {% do log('icecore: warning: the event table could not be looked up: ' ~ out['error'], info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(icecore.event_table_to_read(out.get('value'))) }}
{% endmacro %}


{#- Now on Snowflake's clock as an ISO 8601 UTC string, the form the window readers take. -#}
{% macro _icecore_now_iso() %}
  {{ return(run_query("select to_varchar(sysdate(), 'YYYY-MM-DD\"T\"HH24:MI:SS.FF6\"Z\"')").rows[0][0]) }}
{% endmacro %}


{#- Seconds the history reads look back: var icecore.baseline_lookback_days (default 30), a positive number. A baseline
    older than that is not found, and the run counts as the first. -#}
{% macro _icecore_history_lookback_seconds() %}
  {%- set days = var('icecore', {}).get('baseline_lookback_days', 30) -%}
  {#- a command-line var such as 2e-05 reaches Jinja as text -#}
  {%- set days = (days | float(0)) if days is string else days -%}
  {%- if days is boolean or days is not number or days <= 0 -%}
    {{ exceptions.raise_compiler_error("icecore: baseline_lookback_days must be a positive number of days; got '" ~ days ~ "'") }}
  {%- endif -%}
  {{ return(((days * 86400) | int) if (days * 86400) | int > 0 else 1) }}
{% endmacro %}


{#- The WHERE predicate every history read starts with: the relation first, then the time bound, so the scan of the
    event table is narrowed and does not grow with its age. -#}
{% macro _icecore_history_bound(relation) %}
  {{ return('node_relation = ' ~ icecore._icecore_sql_literal(icecore._icecore_quoted_name(relation))
      ~ ' and event_at >= dateadd(second, -' ~ icecore._icecore_history_lookback_seconds() ~ ', sysdate())') }}
{% endmacro %}


{#- The columns of the newest `icecore.schema_signature` record of the relation, as get_columns_signature returns
    them, or none (no record, no event table, unreadable). -#}
{% macro _icecore_previous_signature(relation) %}
  {%- set event_table = icecore._icecore_history_event_table() -%}
  {%- if event_table is none -%}{{ return(none) }}{%- endif -%}
  {%- set sql -%}
    select attributes:"dbt.schema.columns"::string
    from ({{ icecore.base_events(event_table=event_table) }})
    where {{ icecore._icecore_history_bound(relation) }} and record_name = 'icecore.schema_signature'
    order by event_at desc
    limit 1
  {%- endset -%}
  {%- set raw = icecore._icecore_safe_scalar(sql, 'schema_changes baseline', relation) -%}
  {{ return(fromjson(raw) if raw is string else none) }}
{% endmacro %}


{#- Start of the next change window, as an ISO 8601 UTC string: `since` when given, else the window end (attribute
    dbt.window.until, the event time for records without it) of the newest metric record of the same kind for the relation, else `lookback_hours` before now (Snowflake's clock).
    kind 'column': metrics of one column (column_name) without a dimension; 'dimension': metrics of one column
    with a dimension; 'all_columns': metrics of any column without a dimension. -#}
{% macro _icecore_window_since(relation, kind, column_name, since, lookback_hours) %}
  {%- if since is not none and (since | string | trim) != '' -%}{{ return(since) }}{%- endif -%}
  {%- set iso = 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"' -%}
  {%- set event_table = icecore._icecore_history_event_table() -%}
  {%- set previous = none -%}
  {%- if event_table is not none -%}
    {%- set filters = {
        'column': 'metric_column = ' ~ icecore._icecore_sql_literal(column_name) ~ ' and metric_dimension is null',
        'dimension': 'metric_column = ' ~ icecore._icecore_sql_literal(column_name) ~ ' and metric_dimension is not null',
        'all_columns': 'metric_column is not null and metric_dimension is null'}[kind] -%}
    {%- set sql -%}
      select to_varchar(max(coalesce(convert_timezone('UTC', try_to_timestamp_tz(attributes:"dbt.window.until"::string))::timestamp_ntz, event_at)), '{{ iso }}')
      from ({{ icecore.base_events(event_table=event_table) }})
      where {{ icecore._icecore_history_bound(relation) }} and record_name = 'icecore.metric'
        and {{ filters }}
    {%- endset -%}
    {%- set previous = icecore._icecore_safe_scalar(sql, 'previous observation', relation) -%}
  {%- endif -%}
  {%- if previous is not none -%}{{ return(previous) }}{%- endif -%}
  {{ return(run_query("select to_varchar(dateadd(second, -" ~ ((lookback_hours | float) * 3600) | int ~ ", sysdate()), '" ~ iso ~ "')").rows[0][0]) }}
{% endmacro %}


{#- Compile error unless bounds is none or {metric: {min: n, max: n}} (at least one of the two) over metrics in `names`. -#}
{% macro _icecore_validate_bounds(bounds, names, who) %}
  {%- if bounds is none -%}{{ return(none) }}{%- endif -%}
  {%- if bounds is not mapping -%}
    {{ exceptions.raise_compiler_error(who ~ ": bounds must map a metric to {min, max}, for example {null_rate: {max: 0.05}}") }}
  {%- endif -%}
  {%- for metric, limit in bounds.items() -%}
    {%- if metric not in names -%}
      {{ exceptions.raise_compiler_error(who ~ ": bounds for " ~ metric ~ " but it is not in metrics " ~ names) }}
    {%- endif -%}
    {%- if limit is not mapping or not (limit.keys() | list) or (limit.keys() | list | reject('in', ['min', 'max']) | list) -%}
      {{ exceptions.raise_compiler_error(who ~ ": bounds." ~ metric ~ " must be {min: n, max: n}, with at least one of them") }}
    {%- endif -%}
    {%- for key, number in limit.items() -%}
      {%- if number is boolean or number is not number -%}
        {{ exceptions.raise_compiler_error(who ~ ": bounds." ~ metric ~ "." ~ key ~ " must be a number; got '" ~ number ~ "'") }}
      {%- endif -%}
    {%- endfor -%}
  {%- endfor -%}
{% endmacro %}


{#- True when value is outside limit ({min, max}, either optional); false for no limit or a null value. -#}
{% macro _icecore_outside(value, limit) %}
  {%- if limit is none or value is none -%}{{ return(false) }}{%- endif -%}
  {{ return((limit.get('min') is not none and value < limit['min']) or (limit.get('max') is not none and value > limit['max'])) }}
{% endmacro %}
