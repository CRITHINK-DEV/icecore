{#- Test wrappers for the scale scenarios (tests/test_scale.py). Results are logged as `icecore_it_result:<json>`. -#}

{#- Synthetic edges, appended to the batch like the lineage emitter does (the project's own graph is small). -#}
{% macro it_scale_edges_emitter(results, batch) %}
  {% set edges = [] %}
  {% for i in range(var('it_edges', 0) | int) %}
    {% do edges.append(['model.scale.parent_model_with_a_long_name_' ~ i, 'model.scale.child_model_with_a_long_name_' ~ i]) %}
  {% endfor %}
  {% do icecore._icecore_lineage_append(edges, batch) %}
{% endmacro %}

{#- end_run for `nodes` synthetic model results, sent with emit_run as end_run sends it. Node 0 has a message of
    over_cap_kb KB and node 1 one of near_cap_kb KB (both need var result_message: full). Needs a connection.
    Returns the rows, CALLs and statuses, the invocation id, and the wall time of the two halves. -#}
{% macro it_scale_end_run(nodes, edges=0, near_cap_kb=0, over_cap_kb=0) %}
  {% set results = [] %}
  {#- timings are recent: the event table drops spans that are hours old (measured while writing this) -#}
  {% set started = (modules.datetime.datetime.utcnow() - modules.datetime.timedelta(seconds=5)).strftime('%Y-%m-%dT%H:%M:%S.000000Z') %}
  {% set completed = (modules.datetime.datetime.utcnow() - modules.datetime.timedelta(seconds=2)).strftime('%Y-%m-%dT%H:%M:%S.000000Z') %}
  {% for i in range(nodes | int) %}
    {% set unique_id = 'model.scale.m_' ~ i %}
    {% set message = ('x' * ((over_cap_kb | int) * 1024) if i == 0 and over_cap_kb else
                      ('y' * ((near_cap_kb | int) * 1024) if i == 1 and near_cap_kb else 'SUCCESS 1')) %}
    {% do results.append({'unique_id': unique_id, 'status': 'success', 'execution_time': 0.5,
        'timing': [{'name': 'execute', 'started_at': started, 'completed_at': completed}],
        'adapter_response': {'query_id': '01b-scale-' ~ i, 'rows_affected': 1}, 'message': message, 'failures': none,
        'node': {'unique_id': unique_id, 'name': 'm_' ~ i, 'resource_type': 'model', 'config': {'materialized': 'view'}}}) %}
  {% endfor %}
  {% set emitters = [{'name': 'it_scale_edges', 'var': 'it_edges', 'default': true, 'macro': it_scale_edges_emitter}] %}
  {% set t0 = modules.datetime.datetime.utcnow() %}
  {% set items = icecore._icecore_end_run_items(results, emitters) %}
  {% set t1 = modules.datetime.datetime.utcnow() %}
  {% set sent = icecore.emit_run(items) %}
  {% set t2 = modules.datetime.datetime.utcnow() %}
  {% do _it_result({'invocation_id': invocation_id, 'records': sent['records'], 'calls': sent['calls'],
      'statuses': sent['statuses'], 'build_s': (t1 - t0).total_seconds(), 'send_s': (t2 - t1).total_seconds()}) %}
{% endmacro %}
