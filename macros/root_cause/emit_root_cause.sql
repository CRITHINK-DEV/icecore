{#- UC4 end-of-run emitter `root_cause` (var icecore.root_cause, default false). Called by end_run as
    emit_root_cause(results, batch); a third argument `signals` replaces the read of the views (tests).

    For every failed test of this run it walks the dbt graph upstream (walk_upstream) with two signals read from the
    views in the obs schema, and writes the answer as `dbt.root_cause.unique_id`, `dbt.root_cause.kind` and
    `dbt.root_cause.distance` on that test's node_result LOG (appended earlier in the batch by the node_results
    emitter). When node_results is off, or the test has no LOG, a LOG of its own (`icecore.kind = root_cause`) is
    appended instead. A test with no cause upstream is left as it is.

    Signals (lookback: var icecore.root_cause_lookback_hours, default 24):
      schema_diffs        SCHEMA_CHANGES rows detected in the lookback, per node;
      freshness_breaches  the nodes of icecore.freshness tests that failed or warned in this run (nothing scores
                          freshness yet), and METRICS nodes whose latest `freshness_lag_seconds` in the lookback is an
                          anomaly (empty until a scorer writes one).
    Failures come from this run's results (upstream_failure). The graph is the invocation's own dbt graph: the
    LINEAGE_LATEST view is not read here, because at on-run-end it holds the previous invocation's edges (ingestion
    lag, S3), which can differ from the graph that just ran.

    Records written by this run are not in the views yet (ingestion lag, S3): signals are the ones recorded before
    it. A view that cannot be read (setup not run, no privilege) logs one warning and that signal is empty; the run
    never fails. NODE_EXECUTIONS is not used: node failures are this run's results. Rows added: none when
    node_results is on (the LOGs are annotated), one LOG per explained test otherwise. -#}

{% macro emit_root_cause(results, batch, signals=none) %}
  {%- set failed = icecore._root_cause_failed_tests(results) -%}
  {%- if failed | length == 0 -%}{{ return(none) }}{%- endif -%}
  {%- set signals = signals if signals is not none else icecore._root_cause_signals(results) -%}
  {%- for test_id in failed -%}
    {%- set cause = icecore.walk_upstream(test_id, results, signals) -%}
    {%- if cause is not none -%}
      {%- set attrs = {'dbt.root_cause.unique_id': cause['unique_id'], 'dbt.root_cause.kind': cause['kind'],
                       'dbt.root_cause.distance': cause['distance']} -%}
      {%- set state = {'annotated': false} -%}
      {%- for item in batch['items'] -%}
        {%- if item.get('type') == 'log' and item['attrs'].get('icecore.kind') == 'node_result'
              and item['attrs'].get('dbt.node.unique_id') == test_id -%}
          {%- do item['attrs'].update(attrs) -%}
          {%- do state.update({'annotated': true}) -%}
        {%- endif -%}
      {%- endfor -%}
      {%- if not state['annotated'] -%}
        {%- do attrs.update({'icecore.kind': 'root_cause', 'dbt.node.unique_id': test_id}) -%}
        {%- do batch['items'].append({'type': 'log', 'level': 'error',
            'message': test_id ~ ' failed; root cause ' ~ cause['kind'] ~ ' at ' ~ cause['unique_id'], 'attrs': attrs}) -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
{% endmacro %}


{#- unique_ids of the tests that failed in these results. -#}
{% macro _root_cause_failed_tests(results) %}
  {%- set out = [] -%}
  {%- for result in results or [] -%}
    {%- set node = icecore._icecore_get(result, 'node') -%}
    {%- set uid = icecore._icecore_get(result, 'unique_id') or icecore._icecore_get(node, 'unique_id') -%}
    {%- set status = icecore._icecore_get(result, 'status') | string | lower -%}
    {%- if (icecore._icecore_get(node, 'resource_type') | string) == 'test' and status in ['fail', 'error', 'runtime error'] and uid -%}
      {%- do out.append(uid) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- {'schema_diffs': {unique_id: [changes]}, 'freshness_breaches': {unique_id: detail}} from the obs views. -#}
{% macro _root_cause_signals(results=[]) %}
  {%- set hours = var('icecore', {}).get('root_cause_lookback_hours', 24) | int -%}
  {%- set signals = {'schema_diffs': {}, 'freshness_breaches': {}} -%}
  {%- set changes = icecore._root_cause_read_view('SCHEMA_CHANGES',
      "select array_agg(object_construct('relation', relation, 'unique_id', node_unique_id, 'column', \"COLUMN\", "
      ~ "'change_kind', change_kind, 'from_type', from_type, 'to_type', to_type)) from ",
      " where detected_at >= dateadd(hour, -" ~ hours ~ ", sysdate())") -%}
  {%- for row in changes -%}
    {%- set uid = icecore._root_cause_resolve(row.get('unique_id'), row.get('relation')) -%}
    {%- if uid -%}
      {%- if uid not in signals['schema_diffs'] -%}{%- do signals['schema_diffs'].update({uid: []}) -%}{%- endif -%}
      {%- do signals['schema_diffs'][uid].append({'change_kind': row.get('change_kind'), 'column': row.get('column'),
          'from_type': row.get('from_type'), 'to_type': row.get('to_type')}) -%}
    {%- endif -%}
  {%- endfor -%}
  {#- A failed (or warned) icecore.freshness test of this run is a breach on the node it is attached to: nothing scores
      freshness yet, so the test's own threshold is the verdict. -#}
  {%- for uid in icecore._root_cause_freshness_failures(results) -%}
    {%- do signals['freshness_breaches'].update({uid: {'source': 'icecore.freshness test of this run'}}) -%}
  {%- endfor -%}
  {%- set breaches = icecore._root_cause_read_view('METRICS',
      "select array_agg(object_construct('relation', relation, 'unique_id', node_unique_id, 'value', value, "
      ~ "'observed_at', to_varchar(observed_at))) from (select * from ",
      " where metric = 'freshness_lag_seconds' and observed_at >= dateadd(hour, -" ~ hours ~ ", sysdate()) "
      ~ "qualify row_number() over (partition by coalesce(node_unique_id, relation) order by observed_at desc) = 1) "
      ~ "where anomaly_is_anomaly") -%}
  {%- for row in breaches -%}
    {%- set uid = icecore._root_cause_resolve(row.get('unique_id'), row.get('relation')) -%}
    {%- if uid -%}
      {%- do signals['freshness_breaches'].update({uid: {'value': row.get('value'), 'observed_at': row.get('observed_at')}}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(signals) }}
{% endmacro %}


{#- The unique_ids that failed or warned icecore.freshness tests of these results are attached to. -#}
{% macro _root_cause_freshness_failures(results) %}
  {%- set out = [] -%}
  {%- for result in results or [] -%}
    {%- set node = icecore._icecore_get(result, 'node') -%}
    {%- set uid = icecore._icecore_get(result, 'unique_id') or icecore._icecore_get(node, 'unique_id') -%}
    {%- set status = icecore._icecore_get(result, 'status') | string | lower -%}
    {%- set gnode = graph.nodes.get(uid) if uid else none -%}
    {%- if gnode is not none and status in ['fail', 'warn', 'error'] -%}
      {%- set meta = gnode.get('test_metadata') or {} -%}
      {%- if meta.get('name') == 'freshness' and meta.get('namespace') == 'icecore' -%}
        {%- set attached = gnode.get('attached_node') or ((gnode.get('depends_on') or {}).get('nodes') or [none])[0] -%}
        {%- if attached -%}{%- do out.append(attached) -%}{%- endif -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The rows of one view as a list, or [] with one warning when it cannot be read. -#}
{#- `head` and `tail` surround the view's name in the query. A database or schema that icecore._sql refuses (a `$$`, a control
    character) is a warning and an empty signal, never an exception: this runs at on-run-end. -#}
{% macro _root_cause_read_view(view, head, tail) %}
  {%- set target_view = icecore._icecore_procedure_or_error(view) -%}
  {%- if target_view['error'] -%}
    {% do log('icecore: warning: root cause could not read ' ~ view ~ ': ' ~ target_view['error'], info=True) %}
    {{ return([]) }}
  {%- endif -%}
  {%- set out = icecore._icecore_try_scalar(head ~ target_view['procedure'] ~ tail) -%}
  {%- if 'error' in out -%}
    {% do log('icecore: warning: root cause could not read ' ~ target_view['procedure'] ~ ': ' ~ out['error'] ~ ' (run icecore.setup so the views exist)', info=True) %}
    {{ return([]) }}
  {%- endif -%}
  {%- set value = out.get('value') -%}
  {%- set value = fromjson(value) if value is string else value -%}
  {{ return(value if value else []) }}
{% endmacro %}


{#- The unique_id of a signal row: its node_unique_id, else the graph node or source whose relation matches. -#}
{% macro _root_cause_resolve(unique_id, relation) %}
  {%- if unique_id -%}{{ return(unique_id) }}{%- endif -%}
  {%- if not relation -%}{{ return(none) }}{%- endif -%}
  {%- set wanted = relation | string | replace('"', '') | upper -%}
  {%- for collection in [graph.nodes, graph.sources] -%}
    {%- for uid, node in collection.items() -%}
      {%- if node.get('relation_name') and (node['relation_name'] | string | replace('"', '') | upper) == wanted -%}
        {{ return(uid) }}
      {%- endif -%}
    {%- endfor -%}
  {%- endfor -%}
  {{ return(none) }}
{% endmacro %}
