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
  {%- set out = fromjson(run_query('execute immediate ' ~ icecore._sql(block, 'string')).rows[0][0]) -%}
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
  {{ return('node_relation = ' ~ icecore._sql(icecore._icecore_quoted_name(relation), 'string')
      ~ ' and event_at >= dateadd(second, -' ~ icecore._icecore_history_lookback_seconds() ~ ', sysdate())') }}
{% endmacro %}


{#- The columns of the newest complete `icecore.schema_signature` snapshot of the relation, as get_columns_signature
    returns them, or none (no record, no event table, unreadable, or no snapshot with all its chunks: a
    partial one would read as removed columns, so the newest COMPLETE snapshot is used). A snapshot is the chunks
    (dbt.schema.chunk_index of chunk_count) of one invocation; a record without them is a whole snapshot (older
    records). -#}
{% macro _icecore_previous_signature(relation) %}
  {%- set event_table = icecore._icecore_history_event_table() -%}
  {%- if event_table is none -%}{{ return(none) }}{%- endif -%}
  {%- set sql -%}
    with s as (
      select invocation_id, event_at,
             coalesce(attributes:"dbt.schema.chunk_index"::int, 0) as idx,
             coalesce(attributes:"dbt.schema.chunk_count"::int, 1) as cnt,
             parse_json(attributes:"dbt.schema.columns"::string) as cols
      from ({{ icecore.base_events(event_table=event_table) }})
      where {{ icecore._icecore_history_bound(relation) }} and record_name = 'icecore.schema_signature'
      qualify row_number() over (partition by invocation_id, idx order by event_at desc) = 1
    ), newest as (
      select invocation_id from s group by invocation_id having count(*) = max(cnt)
      order by max(event_at) desc limit 1
    )
    select iff(count(*) = 0, null, array_flatten(array_agg(cols) within group (order by idx))::string)  -- no snapshot: none, not []
    from s where invocation_id = (select invocation_id from newest)
  {%- endset -%}
  {%- set raw = icecore._icecore_safe_scalar(sql, 'schema_changes baseline', relation) -%}
  {{ return(fromjson(raw) if raw is string else none) }}
{% endmacro %}


{#- Start of the next change window, as an ISO 8601 UTC string: `since` when given, else the window end (attribute
    dbt.window.until, the event time for records without it) of the newest metric record of the same kind for the relation, else `lookback_hours` before now (Snowflake's clock).
    kind 'column' (column_anomaly, one column), 'dimension' (dimension_anomaly, one column) or 'all_columns'
    (all_columns_anomaly): the records are told apart by the tag dbt.window.monitor each monitor writes with its
    metrics, so one test's window end never moves another's. Records without the tag are not read. `window_tag` (column_anomaly only)
    narrows it further to the records of one named test (attribute dbt.window.tag), so two column_anomaly tests on the
    same column keep separate windows. -#}
{% macro _icecore_window_since(relation, kind, column_name, since, lookback_hours, window_tag=none) %}
  {%- if since is not none and (since | string | trim) != '' -%}{{ return(since) }}{%- endif -%}
  {%- set iso = 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"' -%}
  {%- set event_table = icecore._icecore_history_event_table() -%}
  {%- set previous = none -%}
  {%- if event_table is not none -%}
    {%- set filters = {
        'column': "attributes:\"dbt.window.monitor\"::string = 'column_anomaly' and metric_column = " ~ icecore._sql(column_name, 'string'),
        'dimension': "attributes:\"dbt.window.monitor\"::string = 'dimension_anomaly' and metric_column = " ~ icecore._sql(column_name, 'string'),
        'all_columns': "attributes:\"dbt.window.monitor\"::string = 'all_columns_anomaly'"}[kind] -%}
    {%- set sql -%}
      select to_varchar(max(coalesce(convert_timezone('UTC', try_to_timestamp_tz(attributes:"dbt.window.until"::string))::timestamp_ntz, event_at)), {{ icecore._sql(iso, 'string') }})
      from ({{ icecore.base_events(event_table=event_table) }})
      where {{ icecore._icecore_history_bound(relation) }} and record_name = 'icecore.metric'
        and {{ filters }}{% if window_tag is not none %}
        and attributes:"dbt.window.tag"::string = {{ icecore._sql(window_tag, 'string') }}{% endif %}
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


{#- window_tag is project config and reaches SQL as a literal: only 1 to 64 letters, digits or underscores (SQL safety
    rule, review of #48). none is allowed. -#}
{% macro _icecore_validate_window_tag(window_tag) %}
  {%- if window_tag is not none and not modules.re.match('^[A-Za-z0-9_]{1,64}\\Z', window_tag | string) -%}
    {{ exceptions.raise_compiler_error("icecore.column_anomaly: window_tag must be 1 to 64 letters, digits or underscores, got " ~ (window_tag | string | tojson)) }}
  {%- endif -%}
  {{ return(true) }}
{% endmacro %}
