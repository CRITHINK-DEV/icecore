{#- Test wrappers for a long run's old node timings (tests/test_old_spans.py). Results are logged as `icecore_it_result:<json>`. -#}

{#- Results of nodes that ran `hours_old` hours ago (one per entry), as run_results.json would list them after a long run. -#}
{% macro _it_old_results(hours_old) %}
  {% set results = [] %}
  {% for hours in hours_old %}
    {% set unique_id = 'model.long.m_' ~ loop.index %}
    {% set started = (modules.datetime.datetime.utcnow() - modules.datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000000Z') %}
    {% set completed = (modules.datetime.datetime.utcnow() - modules.datetime.timedelta(hours=hours) + modules.datetime.timedelta(seconds=3)).strftime('%Y-%m-%dT%H:%M:%S.000000Z') %}
    {% do results.append({'unique_id': unique_id, 'status': 'success', 'execution_time': 3.0,
        'timing': [{'name': 'execute', 'started_at': started, 'completed_at': completed}],
        'adapter_response': {'query_id': '01b-old-' ~ loop.index, 'rows_affected': 1}, 'message': 'SUCCESS 1', 'failures': none,
        'node': {'unique_id': unique_id, 'name': 'm_' ~ loop.index, 'resource_type': 'model', 'config': {'materialized': 'view'}}}) %}
  {% endfor %}
  {{ return(results) }}
{% endmacro %}

{#- What end_run would send for the old results: no connection needed. -#}
{% macro it_old_items(hours_old) %}
  {% do _it_result(icecore._icecore_emit_run_records(icecore._icecore_end_run_items(_it_old_results(hours_old), []))) %}
{% endmacro %}

{#- end_run's own path, sent for real: the batch for the old results, through emit_run. -#}
{% macro it_old_end_run(hours_old) %}
  {% set sent = icecore.emit_run(icecore._icecore_end_run_items(_it_old_results(hours_old), [])) %}
  {% do _it_result({'invocation_id': invocation_id, 'records': sent['records'], 'calls': sent['calls'], 'statuses': sent['statuses']}) %}
{% endmacro %}
