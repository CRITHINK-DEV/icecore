{#-
  UC3 lag-aware metric history reader (spec section 6). Reads the history of ONE metric series, the key
  (relation, metric, column, dimension), from the `metrics` view of the obs schema (macros/views/metrics.sql),
  in the shape icecore.score expects: a SELECT returning `observed_at` (timestamp) and `value` (number).

  Window, on Snowflake's clock in UTC (the event table's):
    observed_at >  now - lookback_days            (default 30 days)
    observed_at <= now - history delay            (var icecore.history_delay_minutes, default 1)
  The delay is the lag rule. Ingestion lag is seconds (spike S3: p95 about 8 s, worst 20 s), so a record newer than
  the delay may be missing or partial, and a history that includes some of those would skew a band. OBSERVED_TIMESTAMP
  is NULL on icecore records (S3), so the rule is applied on `observed_at`, which the metrics view takes from the
  event's TIMESTAMP. Fewer than min_points (default 7) points in the window is "insufficient history": the result
  says so. min_points only sets this flag; the native engine applies its own fixed 7 points when it scores, and
  never flags a series below it (baseline insufficient_history). An error reading the history (missing view, no
  grant) logs a warning and reads as insufficient history, never failing the caller.

  Use:
    {%- set history = icecore.get_metric_history(model, 'row_count') -%}
    {%- set result = icecore.score(history['sql'], value) -%}
-#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {sql, points, min_points, insufficient_history, lookback_days, delay_minutes}. `sql` is the series
    for score; `points` counts the rows of the window. At parse time nothing runs: points and insufficient_history
    are none. -#}
{% macro get_metric_history(relation, metric, column=none, dimension=none, lookback_days=30, delay_minutes=none,
                            min_points=7, source=none, schema=none) %}
  {%- set delay = icecore._icecore_history_delay(delay_minutes) -%}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set sql = icecore.get_metric_history_sql(relation, metric, column, dimension, lookback_days, delay, source, schema) -%}
  {%- set out = {'sql': sql, 'points': none, 'min_points': min_points, 'insufficient_history': none,
                 'lookback_days': lookback_days, 'delay_minutes': delay} -%}
  {%- if execute -%}
    {#- Never fails a user's test: a query that errors (missing view, no grant) warns and reads as insufficient history. -#}
    {%- set counted = icecore._icecore_try_scalar('select count(*) from (' ~ sql ~ ') as history') -%}
    {%- if 'error' in counted -%}
      {%- do out.update({'insufficient_history': true}) -%}
      {%- do log('icecore: warning: get_metric_history could not read the history of ' ~ metric ~ ' on ' ~ name
          ~ ': ' ~ counted['error'] ~ '; treated as insufficient history', info=True) -%}
    {%- else -%}
      {%- set points = counted['value'] | int -%}
      {%- do out.update({'points': points, 'insufficient_history': points < min_points}) -%}
      {%- if points < min_points -%}
        {%- do log('icecore: insufficient history for ' ~ metric ~ ': ' ~ points ~ ' point(s) in ' ~ lookback_days
            ~ ' days, at least ' ~ min_points ~ ' needed', info=True) -%}
      {%- endif -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- The series SELECT alone, no query run. `delay_minutes` is taken as given here (get_metric_history resolves the
    var). `source` is the relation to read, default the obs schema's METRICS view. -#}
{% macro get_metric_history_sql(relation, metric, column=none, dimension=none, lookback_days=30, delay_minutes=1,
                                source=none, schema=none, node_unique_id=none) %}
  {%- if lookback_days is not integer or lookback_days < 1 -%}
    {{ exceptions.raise_compiler_error('icecore: lookback_days must be an integer of at least 1, got ' ~ lookback_days) }}
  {%- endif -%}
  {%- if delay_minutes is not integer or delay_minutes < 0 -%}
    {{ exceptions.raise_compiler_error('icecore: history delay must be an integer of at least 0 minutes, got ' ~ delay_minutes) }}
  {%- endif -%}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set source = source or icecore._icecore_procedure('METRICS', schema) -%}
  {%- set now = "convert_timezone('UTC', current_timestamp())::timestamp_ntz" -%}
  {%- set sql -%}
select observed_at, value
from {{ source }}
where relation = {{ icecore._sql(name, 'string') }}
  and metric = {{ icecore._sql(metric | string, 'string') }}
  and "COLUMN" is not distinct from {{ 'null' if column is none else icecore._sql(column | string, 'string') }}
  and dimension is not distinct from {{ 'null' if dimension is none else icecore._sql(dimension | string, 'string') }}
  and value is not null
  {%- if node_unique_id is not none %}
  and node_unique_id = {{ icecore._sql(node_unique_id | string, 'string') }}
  {%- endif %}
  and observed_at > dateadd(day, -{{ lookback_days }}, {{ now }})
  and observed_at <= dateadd(minute, -{{ delay_minutes }}, {{ now }})
order by observed_at
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The history delay in minutes: the argument, else var icecore.history_delay_minutes (default 1, spike S3). -#}
{% macro _icecore_history_delay(delay_minutes=none) %}
  {{ return(var('icecore', {}).get('history_delay_minutes', 1) if delay_minutes is none else delay_minutes) }}
{% endmacro %}


{#- One history read per run (spikes/GATE2.md, finding 6). on-run-start (icecore.start_run) reads, in one guarded query of
    the METRICS view, the history of every selected icecore monitor test that scores, and keeps it for the run in dbt's
    `graph` under icecore.history.<invocation_id>. graph is the one object dbt hands every hook and node of an
    invocation (Elementary keeps its run cache there too); it is written before any node runs and only read by the
    tests, so threads never race on it. A test then scores its series from that copy (_icecore_history_source) instead
    of reading the view, through the same window as get_metric_history_sql. Nothing breaks without it: when the read
    failed, the test is not in it, or a dbt runtime gave a test another graph, the test reads the view as before.
    The same read takes Snowflake's clock, so the records the tests stamp for end_run are on Snowflake's clock, not
    dbt's (a client clock 18 s ahead was seen); without it they carry dbt's clock, as node spans do. -#}
{% macro _icecore_load_metric_history() %}
  {%- if not execute or graph is not defined or selected_resources is not defined or not icecore._icecore_flag('monitor_score', true) -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set tests = {} -%}
  {%- set names = [] -%}
  {%- set days = namespace(max=30) -%}
  {%- for unique_id in selected_resources if unique_id.startswith('test.') -%}
    {%- set node = graph.nodes.get(unique_id) or {} -%}
    {%- set kind = (node.get('test_metadata') or {}).get('name') -%}
    {%- if kind in ['freshness', 'volume', 'column_anomaly', 'all_columns_anomaly', 'dimension_anomaly', 'schema_changes']
          and ('macro.icecore.test_' ~ kind) in ((node.get('depends_on') or {}).get('macros') or []) -%}
      {%- do tests.update({unique_id: true}) -%}
      {#- the relation a schema_changes test checks, named as its records name it; one the graph names differently misses the copy and reads for itself -#}
      {%- set target_id = node.get('attached_node') or ((node.get('depends_on') or {}).get('nodes') or [none])[0] -%}
      {%- set target_node = graph.nodes.get(target_id) or graph.sources.get(target_id) -%}
      {%- if kind == 'schema_changes' and target_node -%}
        {%- set parts = [target_node.get('database'), target_node.get('schema'), target_node.get('alias') or target_node.get('identifier') or target_node.get('name')] | map('string') | map('upper') | list -%}
        {%- if icecore._sql_error(parts, 'name') is none -%}{%- do names.append(icecore._sql(parts, 'name')) -%}{%- endif -%}
      {%- endif -%}
      {%- set lookback = (node['test_metadata'].get('kwargs') or {}).get('lookback_days') -%}
      {%- if lookback is integer and lookback > days.max -%}{%- set days.max = lookback -%}{%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if not tests -%}{{ return(none) }}{%- endif -%}
  {%- set ids = [] -%}
  {%- for unique_id in tests -%}{%- do ids.append(icecore._sql(unique_id, 'string')) -%}{%- endfor -%}
  {%- set sql -%}
select array_agg(object_construct_keep_null('u', node_unique_id, 'r', relation, 'm', metric, 'c', "COLUMN", 'd', dimension, 'p', p))
from (
  select node_unique_id, relation, metric, "COLUMN", dimension,
         array_agg(array_construct(to_varchar(observed_at, 'YYYY-MM-DD"T"HH24:MI:SS.FF9'), value)) within group (order by observed_at) as p
  from {{ icecore._icecore_procedure('METRICS') }}
  where node_unique_id in ({{ ids | join(', ') }})
    and value is not null
    and observed_at > dateadd(day, -{{ days.max }}, convert_timezone('UTC', current_timestamp())::timestamp_ntz)
  group by node_unique_id, relation, metric, "COLUMN", dimension
)
  {%- endset -%}
  {#- the schema snapshots the schema_changes tests compare with (one read of the event table, for all of them), when the event table is known -#}
  {%- set event_table = icecore._icecore_history_event_table() if names else none -%}
  {#- one guarded statement: Snowflake's clock first, so it is known even when the history cannot be read; each read has its own guard -#}
  {%- set block -%}
declare
  now_text varchar default null;
  v variant default null;
  verr varchar default null;
  sg variant default null;
begin
  now_text := to_varchar(sysdate(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"');
  begin
    select x into :v from (select ({{ sql }})::variant as x);
  exception
    when other then verr := sqlerrm;
  end;
  {%- if event_table is not none %}
  begin
    select x into :sg from (select ({{ icecore._icecore_signatures_sql(event_table, names) }})::variant as x);
  exception
    when other then sg := null;
  end;
  {%- endif %}
  return object_construct('now', now_text, 'value', v, 'error', verr, 'signatures', sg);
exception
  when other then return object_construct('error', sqlerrm);
end;
  {%- endset -%}
  {%- set res = fromjson(run_query('execute immediate ' ~ icecore._sql(block, 'string')).rows[0][0]) or {'error': 'the result is not JSON'} -%}
  {%- set client_ns = icecore._icecore_epoch_ns(modules.datetime.datetime.utcnow()) -%}
  {%- set server_ns = icecore._icecore_epoch_ns(res.get('now')) if res.get('now') is string else none -%}
  {%- set series = {} -%}
  {%- if 'error' in res or (res.get('value') is not none and res.get('value') is string) -%}
    {%- do log('icecore: warning: the metric history of the run could not be read at once (' ~ res.get('error', 'unexpected result') ~ '); each monitor test reads its own', info=True) -%}
    {%- set tests = {} -%}
  {%- else -%}
    {%- for row in res.get('value') or [] -%}
      {%- do series.update({tojson([row['u'], row['r'], row['m'], row['c'], row['d']]): row['p']}) -%}
    {%- endfor -%}
  {%- endif -%}
  {#- a relation is in the copy only when the read worked: a failed read leaves the test to read for itself -#}
  {%- set signatures = {} -%}
  {%- if res.get('signatures') is iterable and res.get('signatures') is not string -%}
    {%- for name in names -%}{%- do signatures.update({name: none}) -%}{%- endfor -%}
    {%- for row in res['signatures'] -%}{%- do signatures.update({row['r']: row['c']}) -%}{%- endfor -%}
  {%- endif -%}
  {%- do graph.setdefault('icecore', {}).setdefault('history', {}).update({invocation_id: {'tests': tests, 'lookback_days': days.max, 'series': series,
      'signatures': signatures, 'clock_offset_ns': 0 if server_ns is none else server_ns - client_ns}}) -%}
{% endmacro %}


{#- The run's copy of one series as the `source` of get_metric_history_sql (a subquery with the METRICS columns it
    filters on), or none when the run has no copy that covers this test and lookback: then the view is read. -#}
{% macro _icecore_history_source(test_id, relation, metric, column, dimension, lookback_days) %}
  {%- set run = ((graph.get('icecore') or {}).get('history') or {}).get(invocation_id) if graph is defined else none -%}
  {%- if run is none or test_id is none or test_id not in run['tests'] or lookback_days > run['lookback_days'] -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set points = run['series'].get(tojson([test_id, relation, metric, column, dimension])) or [] -%}
  {{ return('(select ' ~ icecore._sql(relation, 'string') ~ ' as relation, ' ~ icecore._sql(metric, 'string') ~ ' as metric, '
      ~ ('null' if column is none else icecore._sql(column, 'string')) ~ '::varchar as "COLUMN", '
      ~ ('null' if dimension is none else icecore._sql(dimension, 'string')) ~ '::varchar as dimension, '
      ~ icecore._sql(test_id, 'string') ~ ' as node_unique_id, to_timestamp_ntz(f.value[0]::string, ' ~ icecore._sql('YYYY-MM-DD"T"HH24:MI:SS.FF9', 'string') ~ ') as observed_at, f.value[1]::float as value '
      ~ 'from table(flatten(input => parse_json(' ~ icecore._sql(tojson(points), 'string') ~ '))) as f)') }}
{% endmacro %}


{#- end_run: drop the run's copy (a process that runs dbt again keeps its graph). -#}
{% macro _icecore_forget_metric_history() %}
  {%- if graph is defined -%}
    {%- do ((graph.get('icecore') or {}).get('history') or {}).pop(invocation_id, none) -%}
    {%- do ((graph.get('icecore') or {}).get('event_table') or {}).pop(invocation_id, none) -%}
  {%- endif -%}
{% endmacro %}
