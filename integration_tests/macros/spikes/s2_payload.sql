{#-
  Spike S2: payload limits. Emits one record whose message (logs) or attribute `icecore.payload`
  (span event, Python log attribute) is size_kb x 1024 ASCII characters, through the S1 probes.
  mechanism: 'sql_log' | 'sql_event' | 'python_log' | 'python_attr' (the last one added to the plan's
  three because the S1 recommendation, E2, carries node results as LOG attributes).
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
  {% else %}
    {{ exceptions.raise_compiler_error("s2_payload: unknown mechanism '" ~ mechanism ~ "'") }}
  {% endif %}
{% endmacro %}
