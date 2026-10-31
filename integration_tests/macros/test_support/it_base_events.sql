{#- Fixtures for tests/test_base_events.py: the foreign procedures that base_events must ignore, and one
    run-operation that makes every fixture CALL through the S1 probe's guarded call (s1_emit_call).
    The icecore-shaped procedures come from the S1 probe macros (macros/spikes/s1_emit_proc.sql). -#}

{#- In <target db>.<schema>: OTHER_EMIT_<run_id>, the S1 SQL procedure under a name that is not ICECORE_EMIT*, and
    ICECORE_EMIT_RUN_OTHER_<run_id>, the S1 Python procedure with a tracer of another library (SCOPE other_library). -#}
{% macro it_be_foreign_procs(schema, run_id) %}
  {% if execute %}
    {% set prefix = target.database ~ '.' ~ schema ~ '.' %}
    {% set sql_proc %}
CREATE OR REPLACE PROCEDURE {{ prefix }}OTHER_EMIT_{{ run_id | upper }}(events ARRAY)
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
    {% set py_proc %}
CREATE OR REPLACE PROCEDURE {{ prefix }}ICECORE_EMIT_RUN_OTHER_{{ run_id | upper }}(spans ARRAY)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'opentelemetry-api==1.38.0')
HANDLER = 'run'
EXECUTE AS OWNER
AS $$
from opentelemetry import trace


def run(session, spans):
    tracer = trace.get_tracer("other_library")
    for s in spans:
        span = tracer.start_span(s["name"], start_time=int(s["start_ns"]), attributes=s.get("attrs") or {})
        span.end(end_time=int(s["end_ns"]))
    return "ok " + str(len(spans))
$$
    {% endset %}
    {% do run_query(sql_proc) %}
    {% do run_query(py_proc) %}
  {% endif %}
{% endmacro %}


{#- calls: [{proc, events}]; one guarded CALL each; logs the statuses as icecore_it_result. -#}
{% macro it_be_emit(calls) %}
  {% set statuses = [] %}
  {% for c in calls %}
    {% do statuses.append(s1_emit_call(c['proc'], c['events'])) %}
  {% endfor %}
  {% do log('icecore_it_result:' ~ tojson(statuses), info=True) %}
{% endmacro %}
