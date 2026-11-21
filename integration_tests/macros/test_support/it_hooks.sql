{#- Test wrappers for the UC1 hooks (icecore.start_run, icecore.end_run) and the run emitter (icecore.emit_run).
    Results are logged as `icecore_it_result:<json>` for pytest (tests/test_hooks.py). `results` here are
    run-result dicts shaped like run_results.json, each with its `node`. -#}

{#- The start record as ICECORE_EMIT receives it. -#}
{% macro it_run_start_record() %}
  {% do _it_result(icecore._icecore_emit_records([icecore._icecore_run_start_event()])) %}
{% endmacro %}

{#- The run_start record with the layer standard of IT_PAYLOADS (JSON: YAML vars cannot carry every payload), and the
    guarded CALL statement that would send it (no connection used). -#}
{% macro it_run_start_call() %}
  {% set event = icecore._icecore_run_start_event() %}
  {% do event['attributes'].update({'icecore.layer_standard': icecore._icecore_layer_standard(fromjson(env_var('IT_PAYLOADS')))}) %}
  {% set records = icecore._icecore_emit_records([event]) %}
  {% do _it_result({'record': records[0], 'sql': icecore._icecore_guarded_call_sql(icecore._icecore_procedure('ICECORE_EMIT'), records)}) %}
{% endmacro %}

{#- What each hook renders, and what end_run would send, with no connection used when telemetry is off. -#}
{% macro it_hooks_render(results) %}
  {% do _it_result({
      'start_run': icecore.start_run(),
      'end_run': icecore.end_run(results),
      'telemetry': icecore._icecore_telemetry_enabled(),
  }) %}
{% endmacro %}

{#- end_run's batch for these results, as ICECORE_EMIT_RUN receives it, and how it chunks. -#}
{% macro it_end_run_render(results, max_bytes=786432) %}
  {% set records = icecore._icecore_emit_run_records(icecore._icecore_end_run_items(results)) %}
  {% set chunks = icecore._icecore_emit_run_chunks(records, max_bytes) %}
  {% set sizes, bytes = [], [] %}
  {% for chunk in chunks %}
    {% do sizes.append(chunk | length) %}
    {% do bytes.append(tojson(chunk) | length) %}
  {% endfor %}
  {% do _it_result({
      'records': records, 'chunk_sizes': sizes, 'chunk_bytes': bytes,
      'sql': icecore._icecore_guarded_call_sql(icecore._icecore_procedure('ICECORE_EMIT_RUN'), chunks[0]),
      'run_started_at_ns': icecore._icecore_epoch_ns(run_started_at),
  }) %}
{% endmacro %}

{#- emit_run's records and chunks for arbitrary items. -#}
{% macro it_emit_run_render(items, max_bytes=786432) %}
  {% set records = icecore._icecore_emit_run_records(items) %}
  {% set sizes, bytes = [], [] %}
  {% for chunk in icecore._icecore_emit_run_chunks(records, max_bytes) %}
    {% do sizes.append(chunk | length) %}
    {% do bytes.append(tojson(chunk) | length) %}
  {% endfor %}
  {% do _it_result({'records': records, 'chunk_sizes': sizes, 'chunk_bytes': bytes}) %}
{% endmacro %}

{% macro it_collect_query_ids(result) %}
  {% do _it_result(icecore.collect_query_ids(result)) %}
{% endmacro %}

{#- end_run's emitter list without the macros themselves. -#}
{% macro it_end_run_emitters() %}
  {% set out = [] %}
  {% for emitter in icecore._icecore_end_run_emitters() %}
    {% do out.append({'name': emitter['name'], 'var': emitter['var'], 'default': emitter['default']}) %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}

{#- dbt.result.message as node_attributes writes it, for each message, in the current result_message mode. -#}
{% macro it_result_message(messages) %}
  {% set out = [] %}
  {% for message in messages %}
    {% do out.append(icecore.node_attributes(none, {'message': message, 'status': 'error'}).get('dbt.result.message')) %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}


{#- Monitor records at run end (macros/telemetry/test_records.sql): the comment a test renders for `events`, put in the
    compiled code of a fake test result (unique_id, or `foreign` records of another invocation id), then end_run's batch
    for those results with `span` false dropping the test's timing so it has no node span. -#}
{% macro it_test_records(events, span=true, foreign=false, offset_ns=none) %}
  {% if offset_ns is not none %}
    {% do graph.setdefault('icecore', {}).setdefault('history', {}).update({invocation_id: {'tests': {}, 'lookback_days': 30, 'series': {}, 'clock_offset_ns': offset_ns}}) %}
  {% endif %}
  {% set before_ns = icecore._icecore_epoch_ns(modules.datetime.datetime.utcnow()) %}
  {% set comment = icecore._icecore_records_comment(events) %}
  {% do icecore._icecore_forget_metric_history() %}
  {% if foreign %}{% set comment = comment | replace(invocation_id, 'another-invocation') %}{% endif %}
  {% set timing = [{'name': 'compile', 'started_at': '2026-11-20T10:00:00.000000Z', 'completed_at': '2026-11-20T10:00:01.000000Z'}] if span else [] %}
  {% set results = [{'unique_id': 'test.p.mon', 'status': 'pass', 'timing': timing, 'execution_time': 1, 'adapter_response': {}, 'message': none, 'failures': 0,
                     'node': {'unique_id': 'test.p.mon', 'name': 'mon', 'resource_type': 'test', 'config': {}, 'compiled_code': '  ' ~ comment ~ '\n  select 1 where false'}}] %}
  {% set items = icecore._icecore_end_run_items(results) %}
  {% do _it_result({'comment': comment, 'items': items, 'collects': icecore._icecore_end_run_collects(), 'before_ns': before_ns}) %}
{% endmacro %}


{#- One history read per run (macros/engines/get_metric_history.sql): seed the run's copy as on-run-start leaves it,
    then the source a test gets, and whether end_run forgets it. -#}
{% macro it_history_source(test_id, relation, metric, column=none, dimension=none, lookback_days=30, seed=true, points=none) %}
  {% if seed %}
    {% do graph.setdefault('icecore', {}).setdefault('history', {}).update({invocation_id: {'tests': {'test.p.mon': true}, 'lookback_days': 30,
        'series': {tojson(['test.p.mon', relation, metric, column, dimension]): points or []}}}) %}
  {% endif %}
  {% set source = icecore._icecore_history_source(test_id, relation, metric, column, dimension, lookback_days) %}
  {% do icecore._icecore_forget_metric_history() %}
  {% do _it_result({'source': source, 'sql': none if source is none else icecore.get_metric_history_sql(relation, metric, column, dimension, lookback_days, 1, source, none, test_id),
                    'forgotten': invocation_id not in ((graph.get('icecore') or {}).get('history') or {})}) %}
{% endmacro %}
