{#- Incremental column monitors (UC2): metrics over the rows changed since the previous observation.
    They compute and return values only; emitting METRIC records is the monitors' job (after Gate 1).
    Every query runs inside the catching block of metadata.sql (_icecore_try_scalar / _icecore_safe_scalar), so a
    missing table, column or privilege returns none with a logged warning and never fails the user's run.

    Cost, measured in spike S5: CHANGES is cheap only when the window holds inserts alone (it reads just the new
    micro-partitions). After a DELETE, UPDATE or MERGE (dbt's default incremental strategy on Snowflake) it reads
    the old and the new version of every rewritten micro-partition, about 1.9 times a full scan in S5. The
    timestamp-column fallback costs what the filter prunes, which depends on how the table is clustered. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro get_change_window(relation, since, timestamp_column=none) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set name = icecore._icecore_quoted_name(relation) -%}
  {%- if since is none -%}
    {% do log('icecore: warning: get_change_window needs since (the previous observation time) for ' ~ name, info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {#- Compile-only probe: LIMIT 0 reads no data, but CHANGES fails at compile time when change tracking is off
      or does not cover the window. -#}
  {%- set probe = icecore._icecore_try_scalar(
      'select count(*) from (select 1 from ' ~ icecore._icecore_changes_source(relation, since) ~ ' limit 0)') -%}
  {%- if 'error' not in probe -%}
    {{ return({'mode': 'changes', 'since': since}) }}
  {%- endif -%}
  {%- set unavailable = name ~ ': CHANGES is unavailable since ' ~ since ~ ' (' ~ probe['error'] ~ ')' -%}
  {%- if timestamp_column is none -%}
    {% do log('icecore: warning: no change window for ' ~ unavailable ~ ' and no timestamp_column was given', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- set ts_type = icecore._icecore_column_type(relation, timestamp_column) -%}
  {%- if ts_type not in icecore._icecore_window_timestamp_types() -%}
    {% do log('icecore: warning: no change window for ' ~ unavailable ~ ', and timestamp column ' ~ timestamp_column
        ~ (' was not found' if ts_type is none else ' is a ' ~ ts_type ~ ', not a date or timestamp'), info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {% do log('icecore: ' ~ unavailable ~ '; using timestamp column ' ~ timestamp_column ~ ' (' ~ ts_type ~ ')', info=True) %}
  {{ return({'mode': 'timestamp_column', 'since': since, 'timestamp_column': timestamp_column, 'timestamp_type': ts_type}) }}
{% endmacro %}


{% macro compute_column_metrics(relation, column_name, window, metrics=none) %}
  {%- if not execute or window is none -%}{{ return(none) }}{%- endif -%}
  {%- set names = icecore._icecore_column_metric_names(metrics) -%}
  {%- if names | length == 0 -%}{{ return(none) }}{%- endif -%}
  {%- set value = icecore._icecore_safe_scalar(
      icecore._icecore_column_metrics_sql(relation, column_name, window, names), 'compute_column_metrics', relation) -%}
  {%- if value is none -%}{{ return(none) }}{%- endif -%}
  {%- set parsed = fromjson(value) if value is string else value -%}
  {%- set out = {} -%}
  {%- for metric in names -%}
    {%- set v = parsed.get(metric) -%}
    {%- do out.update({metric: (v | int) if (metric == 'row_count' and v is not none) else v}) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- Metric name -> aggregate over the window rows, where `c` is the quoted column.
    row_count: rows in the window (inserted or updated row versions). It is not the metadata total of the volume
    monitor (`row_count` from get_row_count_metadata) nor the net delta (`rows_added` from get_row_count_changes).
    null_rate: share of those rows where the column is null; null when the window is empty. -#}
{% macro _icecore_column_metric_exprs(c) %}
  {{ return({
      'row_count': 'count(*)',
      'null_rate': '(count_if(' ~ c ~ ' is null) / nullif(count(*), 0))::float',
  }) }}
{% endmacro %}


{% macro _icecore_column_metric_defaults() %}
  {{ return(['row_count', 'null_rate']) }}
{% endmacro %}


{#- The requested metrics that are supported, in request order; unknown names are skipped with a warning. -#}
{% macro _icecore_column_metric_names(metrics) %}
  {%- set requested = icecore._icecore_column_metric_defaults() if metrics is none else metrics -%}
  {%- set supported = icecore._icecore_column_metric_exprs('c') -%}
  {%- set names = [] -%}
  {%- for metric in requested -%}
    {%- if metric not in supported -%}
      {% do log('icecore: warning: compute_column_metrics: unknown metric ' ~ metric ~ ' skipped (supported: ' ~ (supported.keys() | list | join(', ')) ~ ')', info=True) %}
    {%- elif metric not in names -%}
      {%- do names.append(metric) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(names) }}
{% endmacro %}


{% macro _icecore_column_metrics_sql(relation, column_name, window, metrics=none) %}
  {%- set c = icecore._icecore_column_ref(column_name) -%}
  {%- set exprs = icecore._icecore_column_metric_exprs(c) -%}
  {%- set pairs = [] -%}
  {%- for metric in icecore._icecore_column_metric_names(metrics) -%}
    {%- do pairs.append(icecore._icecore_sql_literal(metric) ~ ', ' ~ exprs[metric]) -%}
  {%- endfor -%}
  {{ return('select object_construct_keep_null(' ~ (pairs | join(', ')) ~ ') from ('
      ~ icecore._icecore_window_rows_sql(relation, window, [column_name]) ~ ') w') }}
{% endmacro %}


{#- The rows of the window, with only the given columns (columnar storage: never select *).
    changes: the INSERT row versions over CHANGES (new rows and the new values of updated rows).
    timestamp_column: rows whose timestamp is after `since`; a TIMESTAMP_NTZ column is read as UTC wall-clock time,
    a DATE column from the day of `since` included. -#}
{% macro _icecore_window_rows_sql(relation, window, columns) %}
  {%- set refs = [] -%}
  {%- for col in columns -%}{%- do refs.append(icecore._icecore_column_ref(col)) -%}{%- endfor -%}
  {%- set select_list = refs | join(', ') -%}
  {%- set since = icecore._icecore_sql_literal(window['since']) -%}
  {%- if window['mode'] == 'changes' -%}
    {{ return('select ' ~ select_list ~ ' from ' ~ icecore._icecore_changes_source(relation, window['since'])
        ~ " where metadata$action = 'INSERT'") }}
  {%- endif -%}
  {%- set ts = icecore._icecore_column_ref(window['timestamp_column']) -%}
  {%- set ts_type = window['timestamp_type'] -%}
  {%- if ts_type == 'DATE' -%}
    {%- set predicate = ts ~ ' >= to_timestamp_ntz(' ~ since ~ ')::date' -%}
  {%- elif ts_type == 'TIMESTAMP_NTZ' -%}
    {%- set predicate = ts ~ ' > to_timestamp_ntz(' ~ since ~ ')' -%}
  {%- else -%}
    {%- set predicate = ts ~ ' > to_timestamp_tz(' ~ since ~ ')' -%}
  {%- endif -%}
  {{ return('select ' ~ select_list ~ ' from ' ~ icecore._icecore_quoted_name(relation) ~ ' where ' ~ predicate) }}
{% endmacro %}


{% macro _icecore_window_timestamp_types() %}
  {{ return(['TIMESTAMP_NTZ', 'TIMESTAMP_LTZ', 'TIMESTAMP_TZ', 'DATE']) }}
{% endmacro %}


{#- Column name as Snowflake stores it: a plain identifier (letters, digits, _ and $) resolves to upper case like
    an unquoted name; anything else (spaces, quotes, ...) is taken as the exact, case-preserved name. -#}
{% macro _icecore_column_name(column_name) %}
  {%- if modules.re.match('^[A-Za-z_][A-Za-z0-9_$]*$', column_name) -%}
    {{ return(column_name | upper) }}
  {%- endif -%}
  {{ return(column_name) }}
{% endmacro %}


{% macro _icecore_column_ref(column_name) %}
  {{ return('"' ~ (icecore._icecore_column_name(column_name) | replace('"', '""')) ~ '"') }}
{% endmacro %}


{#- DATA_TYPE of the column from INFORMATION_SCHEMA.COLUMNS (TIMESTAMP_NTZ, NUMBER, TEXT, ...), or none. -#}
{% macro _icecore_column_type(relation, column_name) %}
  {%- set parts = icecore._icecore_name_parts(relation) -%}
  {%- set sql -%}
    select data_type
    from {{ '"' ~ (parts[0] | replace('"', '""')) ~ '"' }}.information_schema.columns
    where table_schema = {{ icecore._icecore_sql_literal(parts[1]) }}
      and table_name = {{ icecore._icecore_sql_literal(parts[2]) }}
      and column_name = {{ icecore._icecore_sql_literal(icecore._icecore_column_name(column_name)) }}
  {%- endset -%}
  {{ return(icecore._icecore_safe_scalar(sql, 'column type', relation)) }}
{% endmacro %}
