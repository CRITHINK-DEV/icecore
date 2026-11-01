{#- Test wrappers for the UC1 writer (icecore.emit) and the procedures it calls. Results are logged as
    `icecore_it_result:<json>` for pytest (tests/test_emit.py). -#}

{% macro _it_result(value) %}
  {% do log('icecore_it_result:' ~ tojson(value), info=True) %}
{% endmacro %}

{% macro it_obs_schema() %}
  {% do _it_result({'name': icecore.obs_schema_name(), 'qualified': icecore.obs_schema()}) %}
{% endmacro %}

{% macro it_procedure_ddl() %}
  {% do _it_result(icecore._icecore_emit_procedures_ddl()) %}
{% endmacro %}

{% macro it_create_emit_procedures() %}
  {% do _it_result(icecore.create_emit_procedures()) %}
{% endmacro %}

{#- Renders without running: the records, how they chunk, and the guarded CALL of the first chunk. -#}
{% macro it_emit_render(events) %}
  {% set records = icecore._icecore_emit_records(events) %}
  {% set chunks = icecore._icecore_emit_chunks(records) %}
  {% set sizes, bytes = [], [] %}
  {% for chunk in chunks %}
    {% do sizes.append(chunk | length) %}
    {% do bytes.append(tojson(chunk) | length) %}
  {% endfor %}
  {% do _it_result({
      'records': records, 'chunk_sizes': sizes, 'chunk_bytes': bytes,
      'cap_bytes': icecore._icecore_payload_cap_bytes(),
      'sql': icecore._icecore_guarded_call_sql(icecore._icecore_procedure('ICECORE_EMIT'), chunks[0]),
  }) %}
{% endmacro %}

{% macro it_emit(kind, message=none, attributes=none, level='info') %}
  {% do _it_result(icecore.emit(kind, message, attributes, level)) %}
{% endmacro %}

{% macro it_emit_events(events) %}
  {% do _it_result(icecore.emit_events(events)) %}
{% endmacro %}

{#- Test only: sets the session's levels OFF (the package never does), then emits in the same session. -#}
{% macro it_emit_levels_off(kind, attributes=none) %}
  {% do run_query("ALTER SESSION SET LOG_LEVEL = 'OFF', TRACE_LEVEL = 'OFF'") %}
  {% set levels = {} %}
  {% for name in ['LOG_LEVEL', 'TRACE_LEVEL'] %}
    {% set row = run_query("SHOW PARAMETERS LIKE '" ~ name ~ "' IN SESSION").rows[0] %}
    {% do levels.update({name: row[1]}) %}
  {% endfor %}
  {% set out = icecore.emit(kind, none, attributes) %}
  {% do run_query("ALTER SESSION UNSET LOG_LEVEL, TRACE_LEVEL") %}
  {% do _it_result({'session': levels, 'emit': out}) %}
{% endmacro %}

{#- One guarded CALL of ICECORE_EMIT_RUN with these items (spans and logs). -#}
{% macro it_emit_run(items) %}
  {% do _it_result(icecore._icecore_guarded_call(icecore._icecore_procedure('ICECORE_EMIT_RUN'), items)) %}
{% endmacro %}

{#- The whole run emitter (chunks, caps, guard) for arbitrary items, as end_run uses it. -#}
{% macro it_emit_run_full(items) %}
  {% do _it_result(icecore.emit_run(items)) %}
{% endmacro %}

{#- dbt reads --args as YAML, which cannot carry every character (lone surrogates, odd escapes), so the special-
    character tests hand the text over in the environment variable IT_WEIRD. -#}
{% macro it_emit_weird(run_id) %}
  {% set weird = env_var('IT_WEIRD') %}
  {% do _it_result(icecore.emit('it_esc', weird, {'it.run_id': run_id, 'it.value': weird, 'it.nested': {'k': weird}})) %}
{% endmacro %}

{% macro it_emit_run_weird(run_id, start_ns) %}
  {% set weird = env_var('IT_WEIRD') %}
  {% do _it_result(icecore.emit_run([
      {'name': 'icecore.node', 'start_ns': start_ns - 3000000000, 'end_ns': start_ns - 2000000000,
       'attrs': {'it.run_id': run_id, 'it.value': weird},
       'events': [{'name': 'icecore.query', 'ts_ns': start_ns - 3000000000, 'attrs': {'it.run_id': run_id, 'it.value': weird}}]},
      {'type': 'log', 'level': 'warn', 'message': weird, 'attrs': {'it.run_id': run_id, 'it.value': weird}},
  ])) %}
{% endmacro %}
