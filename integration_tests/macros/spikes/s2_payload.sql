{#-
  Spike S2: payload limits. Emits one record whose message (logs) or attribute `icecore.payload`
  (span event, Python log attribute) is size_kb x 1024 ASCII characters, through the S1 probes.
  mechanism: 'sql_log' | 'sql_event' | 'python_log' | 'python_attr' | 'proc_event'.
  'python_attr' was added because E2 carries node results as LOG attributes; 'proc_event' is the
  path Gate 1 chose (option B): one SPAN_EVENT through the persisted SQL procedure ICECORE_EMIT
  (probe: ICECORE_EMIT_<run_id>, created and dropped here), called guarded through run_query.
  `via` defaults to 'temp_procedure': S1 found that anonymous statements never reach the database's
  event table, so the anonymous path would measure nothing.
-#}
{% macro s2_payload(run_id, mechanism, size_kb, via='temp_procedure') %}
  {% set payload = 'x' * ((size_kb | int) * 1024) %}
  {% if mechanism == 'sql_log' %}
    {% do s1_sql_log(run_id, 1, payload, via) %}
  {% elif mechanism == 'sql_event' %}
    {% do s1_sql_event(run_id, 1, {'icecore.payload': payload}, via) %}
  {% elif mechanism == 'python_log' %}
    {% do s1_python(run_id, 1, 0, {'icecore.payload_kb': size_kb | int}, payload, via) %}
  {% elif mechanism == 'python_attr' %}
    {% do s1_python(run_id, 1, 0, {'icecore.payload': payload}, 'payload', via) %}
  {% elif mechanism == 'proc_event' %}
    {% do _s2_with_proc(run_id, [{'name': 'icecore.payload', 'attrs': {'icecore.run_id': run_id, 'icecore.payload': payload}}]) %}
  {% else %}
    {{ exceptions.raise_compiler_error("s2_payload: unknown mechanism '" ~ mechanism ~ "'") }}
  {% endif %}
{% endmacro %}


{#- Gate 1 option B: create the probe procedure, make one guarded CALL with these events, drop it. -#}
{% macro _s2_with_proc(run_id, events) %}
  {% if execute %}
    {% set proc = target.database ~ '.' ~ target.schema ~ '.ICECORE_EMIT_' ~ (run_id | upper) %}
    {% do s1_emit_proc_create(run_id) %}
    {% set status = s1_emit_call(proc, events) %}
    {{ log('s2 proc status: ' ~ (status | string)[:200], info=True) }}
    {% do run_query('DROP PROCEDURE IF EXISTS ' ~ proc ~ '(ARRAY)') %}
  {% endif %}
{% endmacro %}


{#- One CALL carrying n_events events, each with an `icecore.payload` attribute of event_kb KB. -#}
{% macro s2_proc_batch(run_id, n_events, event_kb) %}
  {% set payload = 'x' * ((event_kb | int) * 1024) %}
  {% set events = [] %}
  {% for i in range(1, (n_events | int) + 1) %}
    {% do events.append({'name': 'icecore.payload', 'attrs': {'icecore.run_id': run_id, 'icecore.seq': i, 'icecore.payload': payload}}) %}
  {% endfor %}
  {% do _s2_with_proc(run_id, events) %}
{% endmacro %}


{#- ICECORE_EMIT_RUN (Python, option B): one CALL carrying n_spans spans, each with an
    `icecore.payload` attribute of span_kb KB and explicit times (1 s each, from one hour ago). -#}
{% macro s2_emit_run_batch(run_id, n_spans, span_kb, start_ns) %}
  {% if execute %}
    {% set proc = target.database ~ '.' ~ target.schema ~ '.ICECORE_EMIT_RUN_' ~ (run_id | upper) %}
    {% do s1_emit_run_proc_create(run_id) %}
    {% do s1_emit_run_batch(run_id, proc, n_spans, start_ns, 1000000000, {}, span_kb) %}
    {% do run_query('DROP PROCEDURE IF EXISTS ' ~ proc ~ '(ARRAY)') %}
  {% endif %}
{% endmacro %}
