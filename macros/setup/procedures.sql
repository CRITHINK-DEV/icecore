{#-
  The write-path contract of Gate 1 option B (spikes/GATE1.md): the two persisted procedures every icecore record
  is written through, both EXECUTE AS OWNER, in <target.database>.<obs schema>.

  ICECORE_EMIT(events ARRAY)      SQL. One SPAN_EVENT per element {name, attrs} through SYSTEM$ADD_EVENT, named
                                  `icecore.<kind>` (SQL cannot set SCOPE, so it stays `global`). Refuses more than
                                  100 events per CALL: above 128 only the last 128 land (S2.md).
  ICECORE_EMIT_RUN(spans ARRAY)   Python 3.11, packages pinned (an unpinned opentelemetry-api resolved to 1.45.1 and
                                  failed to import, S1.md). Elements are spans {name, start_ns, end_ns, attrs,
                                  events: [{name, ts_ns, attrs}]} written with their explicit times, or logs
                                  {type: 'log', level, message, attrs}. SCOPE `icecore`.

  create_emit_procedures(schema) is called by setup; the obs schema must exist. CREATE OR REPLACE ... COPY GRANTS,
  so a second setup keeps the grants an admin made on the procedures. The DDL is rendered by
  _icecore_emit_procedures_ddl() so it can be checked without a connection. Measured in S1/S2
  (integration_tests/macros/spikes/s1_emit_proc.sql, s1_python.sql, s2_payload.sql).
-#}

{#- The obs schema's name, as Snowflake resolves an unquoted identifier (upper case): `schema` when given (setup's
    and doctor's argument), else the obs_schema var. -#}
{% macro obs_schema_name(schema=none) %}
  {{ return((schema or var('icecore', {}).get('obs_schema', 'ICECORE_OBS')) | string | upper) }}
{% endmacro %}


{#- The obs schema, fully qualified and quoted: "<TARGET_DB>"."<OBS_SCHEMA>". -#}
{% macro obs_schema(schema=none) %}
  {%- set parts = [target.database | string | upper, icecore.obs_schema_name(schema)] -%}
  {%- set quoted = [] -%}
  {%- for part in parts -%}
    {%- do quoted.append('"' ~ (part | replace('"', '""')) ~ '"') -%}
  {%- endfor -%}
  {{ return(quoted | join('.')) }}
{% endmacro %}


{#- A procedure of the obs schema, fully qualified (name only, no signature). -#}
{% macro _icecore_procedure(name, schema=none) %}
  {{ return(icecore.obs_schema(schema) ~ '."' ~ name ~ '"') }}
{% endmacro %}


{% macro create_emit_procedures(schema=none) %}
  {%- set created = [] -%}
  {%- if execute -%}
    {%- for ddl in icecore._icecore_emit_procedures_ddl(schema) -%}
      {%- do run_query(ddl) -%}
    {%- endfor -%}
    {%- for name in ['ICECORE_EMIT', 'ICECORE_EMIT_RUN'] -%}
      {%- do created.append(icecore._icecore_procedure(name, schema)) -%}
    {%- endfor -%}
    {%- do log('icecore: created ' ~ (created | join(' and ')), info=True) -%}
  {%- endif -%}
  {{ return(created) }}
{% endmacro %}


{% macro _icecore_emit_procedures_ddl(schema=none) %}
  {%- set emit -%}
CREATE OR REPLACE PROCEDURE {{ icecore._icecore_procedure('ICECORE_EMIT', schema) }}(events ARRAY)
COPY GRANTS
RETURNS STRING
LANGUAGE SQL
COMMENT = 'icecore: writes icecore.<kind> span events (Gate 1 option B)'
EXECUTE AS OWNER
AS $$
DECLARE
  n INTEGER DEFAULT 0;
  nm STRING;
  a OBJECT;
BEGIN
  n := COALESCE(ARRAY_SIZE(events), 0);
  IF (n > 100) THEN
    RETURN 'icecore-error too_many_events: ' || n || ' events in one CALL, at most 100';
  END IF;
  IF (n = 0) THEN
    RETURN 'ok 0';
  END IF;
  FOR i IN 0 TO n - 1 DO
    nm := COALESCE(events[i]:name::STRING, 'icecore.event');
    IF (NOT STARTSWITH(nm, 'icecore.')) THEN
      nm := 'icecore.' || nm;
    END IF;
    a := COALESCE(events[i]:attrs::OBJECT, OBJECT_CONSTRUCT());
    SYSTEM$ADD_EVENT(:nm, :a);
  END FOR;
  RETURN 'ok ' || n;
END;
$$
  {%- endset -%}
  {%- set emit_run -%}
CREATE OR REPLACE PROCEDURE {{ icecore._icecore_procedure('ICECORE_EMIT_RUN', schema) }}(spans ARRAY)
COPY GRANTS
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python', 'opentelemetry-api==1.38.0')
HANDLER = 'run'
COMMENT = 'icecore: writes a run''s spans and logs with explicit times (Gate 1 option B)'
EXECUTE AS OWNER
AS $$
import logging

from opentelemetry import trace

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING,
          "warning": logging.WARNING, "error": logging.ERROR}


def _attrs(item):
    # icecore keys are dotted; an undotted key could clash with a LogRecord field, so it is namespaced.
    return {(k if "." in k else "icecore." + k): v for k, v in (item.get("attrs") or {}).items()}


def run(session, spans):
    tracer = trace.get_tracer("icecore")
    log = logging.getLogger("icecore")
    n_spans = n_logs = 0
    for item in spans or []:
        if item.get("type") == "log":
            level = LEVELS.get(str(item.get("level") or "info").lower(), logging.INFO)
            log.log(level, str(item.get("message") or ""), extra=_attrs(item))
            n_logs += 1
            continue
        start_ns = int(item["start_ns"])
        span = tracer.start_span(item.get("name") or "icecore.span", start_time=start_ns, attributes=_attrs(item))
        for event in item.get("events") or []:
            span.add_event(event.get("name") or "icecore.event", _attrs(event),
                           timestamp=int(event.get("ts_ns") or start_ns))
        span.end(end_time=int(item.get("end_ns") or start_ns))
        n_spans += 1
    return "ok spans=%d logs=%d" % (n_spans, n_logs)
$$
  {%- endset -%}
  {{ return([emit | trim, emit_run | trim]) }}
{% endmacro %}
