{#-
  Spike S1, Gate 1 option B: one persisted SQL procedure, EXECUTE AS OWNER, that turns an ARRAY of
  events {name, attrs} into SPAN_EVENT records with SYSTEM$ADD_EVENT. The shipped one will be
  <target_db>.ICECORE_OBS.ICECORE_EMIT created by icecore.setup; the probe is
  <target db>.<target schema>.ICECORE_EMIT_<run_id>, dropped by the test.

  Calls go through run_query. With guard=true the CALL is wrapped in an anonymous block with an
  EXCEPTION handler, so a missing procedure or a missing grant returns a status instead of raising;
  the macro then logs a warning and the dbt run continues.
-#}

{% macro s1_emit_proc_create(run_id) %}
  {% if execute %}
    {% set sql %}
CREATE OR REPLACE PROCEDURE {{ target.database }}.{{ target.schema }}.ICECORE_EMIT_{{ run_id | upper }}(events ARRAY)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
AS $$
DECLARE
  nm STRING;
  a OBJECT;
BEGIN
  FOR i IN 0 TO ARRAY_SIZE(events) - 1 DO
    nm := events[i]:name::STRING;
    a := events[i]:attrs::OBJECT;
    SYSTEM$ADD_EVENT(:nm, :a);
  END FOR;
  RETURN 'ok ' || ARRAY_SIZE(events);
END;
$$
    {% endset %}
    {% do run_query(sql) %}
  {% endif %}
{% endmacro %}


{% macro _s1_emit_events(run_id, n, attrs, name='icecore.probe', start=1) %}
  {% set out = [] %}
  {% for i in range(start, start + (n | int)) %}
    {% set a = {} %}
    {% do a.update(attrs or {}) %}
    {% do a.update({'icecore.run_id': run_id, 'icecore.seq': i}) %}
    {% do out.append({'name': name, 'attrs': a}) %}
  {% endfor %}
  {{ return(out) }}
{% endmacro %}


{% macro _s1_emit_sql(proc, events, guard=true) %}
  {% set call = 'CALL ' ~ proc ~ '(PARSE_JSON(' ~ s1_sql_literal(tojson(events)) ~ '))' %}
  {% if not guard %}{{ return(call) }}{% endif %}
  {% set sql %}
EXECUTE IMMEDIATE $$
DECLARE
  r STRING;
BEGIN
  {{ call }} INTO :r;
  RETURN r;
EXCEPTION
  WHEN OTHER THEN RETURN 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
END;
$$
  {% endset %}
  {{ return(sql) }}
{% endmacro %}


{#- One CALL; returns the procedure's status. A guarded failure is logged as a warning, never raised. -#}
{% macro s1_emit_call(proc, events, guard=true) %}
  {% if execute %}
    {% set status = run_query(_s1_emit_sql(proc, events, guard)).columns[0].values()[0] %}
    {% if status is string and status.startswith('icecore-error') %}
      {% do exceptions.warn('icecore: telemetry not written (' ~ proc ~ '): ' ~ status) %}
    {% endif %}
    {{ return(status) }}
  {% endif %}
{% endmacro %}


{% macro s1_emit_per_event(run_id, proc, n, attrs={}, guard=true) %}
  {% for e in _s1_emit_events(run_id, n, attrs) %}
    {% do s1_emit_call(proc, [e], guard) %}
  {% endfor %}
{% endmacro %}


{% macro s1_emit_batch(run_id, proc, n, attrs={}, guard=true) %}
  {% do s1_emit_call(proc, _s1_emit_events(run_id, n, attrs), guard) %}
{% endmacro %}


{% macro s1_emit_rollback(run_id, proc) %}
  {% if execute %}
    {% do run_query('BEGIN TRANSACTION') %}
    {% set before = run_query('SELECT CURRENT_TRANSACTION()').columns[0].values()[0] %}
    {% do s1_emit_call(proc, _s1_emit_events(run_id, 1, {'icecore.rollback': true}, 'icecore.probe', 1)) %}
    {% do s1_emit_call(proc, _s1_emit_events(run_id, 1, {'icecore.rollback': true}, 'icecore.probe', 2)) %}
    {% set after = run_query('SELECT CURRENT_TRANSACTION()').columns[0].values()[0] %}
    {% do run_query('ROLLBACK') %}
    {{ log('s1_emit_rollback transaction before=' ~ before ~ ' after=' ~ after, info=True) }}
    {% if before is none or before != after %}
      {{ exceptions.raise_compiler_error('s1_emit_rollback: not inside one open transaction (before=' ~ before ~ ', after=' ~ after ~ ')') }}
    {% endif %}
  {% endif %}
{% endmacro %}


{% macro s1_emit_levels_off(run_id, proc) %}
  {% if execute %}
    {% do run_query("ALTER SESSION SET LOG_LEVEL = 'OFF', TRACE_LEVEL = 'OFF'") %}
    {% do s1_emit_call(proc, _s1_emit_events(run_id, 1, {'icecore.levels': 'off'})) %}
  {% endif %}
{% endmacro %}
