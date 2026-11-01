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
  {#- Compile-time screen only: LIMIT 0 reads no data and fails when change tracking is off. It does NOT prove that
      change tracking covers the window: a table re-created after since (dbt full refresh, table models) or with
      change tracking enabled after rows were written since then passes here and fails only when the CHANGES
      query runs. The compute macros handle that (_icecore_window_scalar) and fall back to timestamp_column then.
      A runnable probe would have to read the change data, which is the expensive part (S5). -#}
  {%- set probe = icecore._icecore_try_scalar(
      'select count(*) from (select 1 from ' ~ icecore._icecore_changes_source(relation, since) ~ ' limit 0)') -%}
  {%- if 'error' not in probe -%}
    {%- set window = {'mode': 'changes', 'since': since} -%}
    {%- if timestamp_column is not none -%}{%- do window.update({'timestamp_column': timestamp_column}) -%}{%- endif -%}
    {{ return(window) }}
  {%- endif -%}
  {{ return(icecore._icecore_timestamp_window(relation, since, timestamp_column,
      name ~ ': CHANGES is unavailable since ' ~ since ~ ' (' ~ probe['error'] ~ ')')) }}
{% endmacro %}


{% macro compute_column_metrics(relation, column_name, window, metrics=none) %}
  {%- if column_name is none -%}
    {{ exceptions.raise_compiler_error('compute_column_metrics needs a column_name for ' ~ icecore._icecore_quoted_name(relation)) }}
  {%- endif -%}
  {%- if not execute or window is none -%}{{ return(none) }}{%- endif -%}
  {%- set names = icecore._icecore_column_metric_names(metrics) -%}
  {%- set numeric = icecore._icecore_numeric_metric_names() -%}
  {%- set asked_numeric = [] -%}
  {%- set others = [] -%}
  {%- for metric in names -%}
    {%- do (asked_numeric if metric in numeric else others).append(metric) -%}
  {%- endfor -%}
  {%- if asked_numeric -%}
    {%- set col_type = icecore._icecore_column_type(relation, column_name) -%}
    {%- if col_type not in icecore._icecore_numeric_types() -%}
      {% do log('icecore: warning: compute_column_metrics: ' ~ (asked_numeric | join(', ')) ~ ' skipped for column ' ~ column_name
          ~ ' of ' ~ icecore._icecore_quoted_name(relation) ~ ': '
          ~ ('column not found' if col_type is none else col_type ~ ' is not numeric'), info=True) %}
      {%- set names = others -%}
    {%- endif -%}
  {%- endif -%}
  {%- if names | length == 0 -%}{{ return({}) }}{%- endif -%}
  {%- set value = icecore._icecore_window_scalar(
      relation, window, 'compute_column_metrics', column_name, names) -%}
  {%- if value is none -%}{{ return(none) }}{%- endif -%}
  {%- set parsed = fromjson(value) if value is string else value -%}
  {%- set out = {} -%}
  {%- for metric in names -%}
    {%- set v = parsed.get(metric) -%}
    {%- do out.update({metric: (v | int) if (metric == 'window_rows' and v is not none) else v}) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- Metric name -> aggregate over the window rows, where `c` is the quoted column.
    window_rows: rows in the window (inserted or updated row versions). Named so that it never collides with the
    volume monitor's `row_count` (metadata total) or `rows_added` (net delta from get_row_count_changes).
    null_rate: share of those rows where the column is null; null when the window is empty.
    avg: over the non-null values of a numeric column, as FLOAT.
    min, max: the same values in the column's own type (a NUMBER stays exact above 2^53; a FLOAT stays a FLOAT).
    Null when there are no non-null values. -#}
{% macro _icecore_column_metric_exprs(c) %}
  {{ return({
      'window_rows': 'count(*)',
      'null_rate': 'count_if(' ~ c ~ ' is null)::float / nullif(count(*), 0)',
      'avg': 'avg(' ~ c ~ ')::float',
      'min': 'min(' ~ c ~ ')',
      'max': 'max(' ~ c ~ ')',
  }) }}
{% endmacro %}


{#- Metrics that need a numeric column; requested on another type they are skipped with a warning. -#}
{% macro _icecore_numeric_metric_names() %}
  {{ return(['avg', 'min', 'max']) }}
{% endmacro %}


{#- INFORMATION_SCHEMA.COLUMNS.DATA_TYPE of numeric columns (NUMBER covers DECIMAL and INT, FLOAT covers DOUBLE and REAL). -#}
{% macro _icecore_numeric_types() %}
  {{ return(['NUMBER', 'FLOAT']) }}
{% endmacro %}


{% macro _icecore_column_metric_defaults() %}
  {{ return(['window_rows', 'null_rate']) }}
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


{#- The timestamp-column window, or none with one warning saying why not. `reason` says why CHANGES is not used
    and is logged with the outcome. -#}
{% macro _icecore_timestamp_window(relation, since, timestamp_column, reason) %}
  {%- if timestamp_column is none -%}
    {% do log('icecore: warning: no change window for ' ~ reason ~ ' and no timestamp_column was given', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- set ts_type = icecore._icecore_column_type(relation, timestamp_column) -%}
  {%- if ts_type not in icecore._icecore_window_timestamp_types() -%}
    {% do log('icecore: warning: no change window for ' ~ reason ~ ', and timestamp column ' ~ timestamp_column
        ~ (' was not found' if ts_type is none else ' is a ' ~ ts_type ~ ', not a date or timestamp'), info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {% do log('icecore: ' ~ reason ~ '; using timestamp column ' ~ timestamp_column ~ ' (' ~ ts_type ~ ')', info=True) %}
  {{ return({'mode': 'timestamp_column', 'since': since, 'timestamp_column': timestamp_column, 'timestamp_type': ts_type}) }}
{% endmacro %}


{#- The SQL of the one-value query for `what`, over `window`: an explicit branch, not a macro looked up by name. -#}
{% macro _icecore_window_query(what, relation, column, window, extra) %}
  {%- if what == 'compute_column_metrics' -%}
    {{ return(icecore._icecore_column_metrics_sql(relation, column, window, extra)) }}
  {%- endif -%}
  {{ return(icecore._icecore_dimension_metrics_sql(relation, column, window, extra)) }}
{% endmacro %}


{#- SQLCODEs of a CHANGES query whose window change tracking does not cover: 707 (000707, time travel data not
    available: table re-created after since, retention exceeded) and 91930 (091930, change tracking enabled
    after the window started). Any other error is not helped by a timestamp window. -#}
{% macro _icecore_changes_window_error_codes() %}
  {{ return([707, 91930]) }}
{% endmacro %}


{#- Run the one-value query of `what` (compute_column_metrics or compute_dimension_metrics) over the window.
    A column that is not found, or whose INFORMATION_SCHEMA read fails (logged as a read failure), returns none at once (no query, no fallback). When a CHANGES query fails with a
    window-coverage code (see _icecore_changes_window_error_codes) and the window carries a timestamp_column, the
    query runs again over the timestamp-column window, and the window dict is switched to it in place: later
    calls with the same window skip CHANGES, so the fallback happens once per window. Returns the value, or none
    with a logged warning; never raises. -#}
{% macro _icecore_window_scalar(relation, window, what, column, extra) %}
  {%- set col = icecore._icecore_column_type_read(relation, column) -%}
  {%- if 'error' in col -%}
    {% do log('icecore: warning: ' ~ what ~ ' could not read the columns of ' ~ icecore._icecore_quoted_name(relation) ~ ': ' ~ col['error'], info=True) %}
    {{ return(none) }}
  {%- elif col.get('value') is none -%}
    {% do log('icecore: warning: ' ~ what ~ ' could not read ' ~ icecore._icecore_quoted_name(relation) ~ ': column ' ~ column ~ ' not found', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- set out = icecore._icecore_try_scalar(icecore._icecore_window_query(what, relation, column, window, extra)) -%}
  {%- if 'error' in out and window['mode'] == 'changes' and window.get('timestamp_column') is not none
        and out.get('code') in icecore._icecore_changes_window_error_codes() -%}
    {%- set ts_window = icecore._icecore_timestamp_window(relation, window['since'], window['timestamp_column'],
        icecore._icecore_quoted_name(relation) ~ ': the CHANGES query failed (' ~ out['error'] ~ ')') -%}
    {%- if ts_window is not none -%}
      {%- do window.update(ts_window) -%}
      {%- set out = icecore._icecore_try_scalar(icecore._icecore_window_query(what, relation, column, window, extra)) -%}
    {%- endif -%}
  {%- endif -%}
  {%- if 'error' in out -%}
    {% do log('icecore: warning: ' ~ what ~ ' could not read ' ~ icecore._icecore_quoted_name(relation) ~ ': ' ~ out['error'], info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(out.get('value')) }}
{% endmacro %}


{#- The rows of the window, with only the given columns (columnar storage: never select *).
    changes: the INSERT row versions over CHANGES (new rows and the new values of updated rows).
    timestamp_column: rows whose timestamp is at or after `since` (CHANGES AT(TIMESTAMP => since) includes `since` too); a TIMESTAMP_NTZ column is read as UTC wall-clock time.
    A DATE column has day granularity: the window starts at the day of `since`, so rows of that day are counted
    again on every run that day. Prefer a timestamp column. -#}
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
    {%- set predicate = ts ~ ' >= to_timestamp_ntz(' ~ since ~ ')' -%}
  {%- else -%}
    {%- set predicate = ts ~ ' >= to_timestamp_tz(' ~ since ~ ')' -%}
  {%- endif -%}
  {{ return('select ' ~ select_list ~ ' from ' ~ icecore._icecore_quoted_name(relation) ~ ' where ' ~ predicate) }}
{% endmacro %}


{% macro _icecore_window_timestamp_types() %}
  {{ return(['TIMESTAMP_NTZ', 'TIMESTAMP_LTZ', 'TIMESTAMP_TZ', 'DATE']) }}
{% endmacro %}


{#- Column name as Snowflake stores it:
    - wrapped in double quotes ("amount", "Mixed", "Net ""Amount"""): the exact name, quotes stripped, inner doubled
      quotes un-doubled, case kept;
    - a plain identifier (letters, digits, _ and $: amount, Mixed): upper case, like an unquoted name;
    - anything else (Net Amount): taken as the exact, case-preserved name. -#}
{% macro _icecore_column_name(column_name) %}
  {%- if column_name | length > 2 and column_name.startswith('"') and column_name.endswith('"') -%}
    {{ return(column_name[1:-1] | replace('""', '"')) }}
  {%- endif -%}
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
  {%- set out = icecore._icecore_column_type_read(relation, column_name) -%}
  {%- if 'error' in out -%}
    {% do log('icecore: warning: column type could not read ' ~ icecore._icecore_quoted_name(relation) ~ ': ' ~ out['error'], info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(out.get('value')) }}
{% endmacro %}


{#- The same read as {'value': <type or none>} or {'error': <message>}: a read that failed is not "column not found". -#}
{% macro _icecore_column_type_read(relation, column_name) %}
  {%- set parts = icecore._icecore_name_parts(relation) -%}
  {%- set sql -%}
    select data_type
    from {{ '"' ~ (parts[0] | replace('"', '""')) ~ '"' }}.information_schema.columns
    where table_schema = {{ icecore._icecore_sql_literal(parts[1]) }}
      and table_name = {{ icecore._icecore_sql_literal(parts[2]) }}
      and column_name = {{ icecore._icecore_sql_literal(icecore._icecore_column_name(column_name)) }}
  {%- endset -%}
  {{ return(icecore._icecore_try_scalar(sql)) }}
{% endmacro %}


{#- Dimension metrics ---------------------------------------------------------------------------- -#}

{% macro compute_dimension_metrics(relation, dimension_column, window, max_values=none) %}
  {%- if dimension_column is none -%}
    {{ exceptions.raise_compiler_error('compute_dimension_metrics needs a dimension_column for ' ~ icecore._icecore_quoted_name(relation)) }}
  {%- endif -%}
  {%- if not execute or window is none -%}{{ return(none) }}{%- endif -%}
  {%- set cap = icecore._icecore_dimension_max_values(max_values) -%}
  {%- set value = icecore._icecore_window_scalar(
      relation, window, 'compute_dimension_metrics', dimension_column, cap) -%}
  {%- if value is none -%}{{ return(none) }}{%- endif -%}
  {%- set parsed = fromjson(value) if value is string else value -%}
  {%- set out = [] -%}
  {%- for row in parsed -%}
    {%- do out.append({'value': row.get('value'), 'window_rows': row['window_rows'] | int, 'other': row['other']}) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The cap: the argument, else vars: icecore: dimension_max_values (default 20, as in dbt_project.yml). -#}
{% macro _icecore_dimension_max_values(max_values) %}
  {%- set cap = max_values if max_values is not none else var('icecore', {}).get('dimension_max_values', 20) -%}
  {{ return([cap | int, 1] | max) }}
{% endmacro %}


{#- One array of {value, window_rows, other}: the `cap` most frequent values (as strings; null is a value of its own),
    then one `other` entry summing the rest. Ordered by other, window_rows desc, value. Empty array for no rows. -#}
{% macro _icecore_dimension_metrics_sql(relation, dimension_column, window, cap) %}
  {%- set d = icecore._icecore_column_ref(dimension_column) -%}
  {%- set cap = cap | int -%}
  {{ return(
"select coalesce(array_agg(object_construct_keep_null('value', value, 'window_rows', window_rows, 'other', other))
         within group (order by other, window_rows desc, value nulls last), array_construct())
from (
  select iff(rk <= " ~ cap ~ ", value, null) as value, rk > " ~ cap ~ " as other, sum(n) as window_rows
  from (
    select value, n, row_number() over (order by n desc, value nulls last) as rk
    from (select to_varchar(" ~ d ~ ") as value, count(*) as n from (" ~ icecore._icecore_window_rows_sql(relation, window, [dimension_column]) ~ ") w group by 1)
  )
  group by 1, 2
)") }}
{% endmacro %}
