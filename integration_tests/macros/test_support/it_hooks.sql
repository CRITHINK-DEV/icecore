{#- Test wrappers for the UC1 hooks (icecore.start_run, icecore.end_run) and the run emitter (icecore.emit_run).
    Results are logged as `icecore_it_result:<json>` for pytest (tests/test_hooks.py). `results` here are
    run-result dicts shaped like run_results.json, each with its `node`. -#}

{#- The start record as ICECORE_EMIT receives it. -#}
{% macro it_run_start_record() %}
  {% do _it_result(icecore._icecore_emit_records([icecore._icecore_run_start_event()])) %}
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
{% macro it_end_run_render(results, max_bytes=524288) %}
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
{% macro it_emit_run_render(items, max_bytes=524288) %}
  {% set records = icecore._icecore_emit_run_records(items) %}
  {% set sizes, bytes = [], [] %}
  {% for chunk in icecore._icecore_emit_run_chunks(records, max_bytes) %}
    {% do sizes.append(chunk | length) %}
    {% do bytes.append(tojson(chunk) | length) %}
  {% endfor %}
  {% do _it_result({'records': records, 'chunk_sizes': sizes, 'chunk_bytes': bytes}) %}
{% endmacro %}
