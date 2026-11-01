{#-
  The write-path contract of Gate 1 option B (spikes/GATE1.md): the persisted procedures every icecore record
  is written through, all EXECUTE AS OWNER, in <target.database>.<obs schema>. Two more are the only
  writers of their record: ICECORE_ACK of `icecore.ack` (UC6 acknowledgements; ICECORE_EMIT refuses that name) and
  ICECORE_NOTIFIED of `icecore.notified` (UC6 notification records; ICECORE_EMIT and ICECORE_EMIT_RUN refuse that name).

  ICECORE_EMIT(events ARRAY)      SQL. One SPAN_EVENT per element {name, attrs} through SYSTEM$ADD_EVENT, named
                                  `icecore.<kind>` (SQL cannot set SCOPE, so it stays `global`). Refuses more than
                                  100 events per CALL: above 128 only the last 128 land (S2.md). Refuses the whole
                                  CALL when any event is named `icecore.ack`.
  ICECORE_EMIT_RUN(spans ARRAY)   Python 3.11, packages pinned (an unpinned opentelemetry-api resolved to 1.45.1 and
                                  failed to import, S1.md). Elements are spans {name, start_ns, end_ns, attrs,
                                  events: [{name, ts_ns, attrs}]} written with their explicit times, or logs
                                  {type: 'log', level, message, attrs}. SCOPE `icecore`.
  ICECORE_NOTIFIED(incident_key STRING, channel STRING) SQL. Records that an alert notified one incident on one
                                  channel: the span event `icecore.notified` with icecore.notified.incident_key,
                                  .channel and .by (CURRENT_USER()). Refuses a key that is not 32 hex characters and a
                                  channel that is not a plain name. Names no object, takes no object name. USAGE goes only
                                  to the role that owns the alerts, never to the dbt role.
  ICECORE_ACK(incident_key STRING) SQL. Acknowledges one incident: refuses a key that is not a current
                                  unacknowledged incident of the ALERTS_FEED view, then writes the span event
                                  `icecore.ack` with icecore.ack.incident_key and icecore.ack.by = CURRENT_USER()
                                  (the caller; never an argument). Its USAGE is granted separately from the emitters.

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
    {#- A changed signature would CREATE OR REPLACE a second overload and leave the old one callable: drop the older ones first. -#}
    {%- for old in icecore._icecore_superseded_signatures(schema) -%}
      {%- do run_query(old) -%}
    {%- endfor -%}
    {%- for ddl in icecore._icecore_emit_procedures_ddl(schema) -%}
      {%- do run_query(ddl) -%}
    {%- endfor -%}
    {%- for name in ['ICECORE_EMIT', 'ICECORE_EMIT_RUN', 'ICECORE_ACK', 'ICECORE_NOTIFIED'] -%}
      {%- do created.append(icecore._icecore_procedure(name, schema)) -%}
    {%- endfor -%}
    {%- do log('icecore: created ' ~ (created | join(', ')), info=True) -%}
  {%- endif -%}
  {{ return(created) }}
{% endmacro %}


{#- DROP PROCEDURE IF EXISTS statements for signatures an earlier version of icecore created and a later one no longer
    has (none yet: the current signatures are ICECORE_EMIT(ARRAY), ICECORE_EMIT_RUN(ARRAY), ICECORE_ACK(STRING) and
    ICECORE_NOTIFIED(STRING, STRING)). A change to one of them adds the old signature here. -#}
{% macro _icecore_superseded_signatures(schema=none) %}
  {{ return([]) }}
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
    IF (LOWER(nm) = 'icecore.ack') THEN
      RETURN 'icecore-error forbidden_event: icecore.ack is written by ICECORE_ACK only';
    END IF;
    IF (LOWER(nm) = 'icecore.notified') THEN
      RETURN 'icecore-error forbidden_event: icecore.notified is written by ICECORE_NOTIFIED only';
    END IF;
  END FOR;
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
    for item in spans or []:
        names = [item.get("name")] + [e.get("name") for e in item.get("events") or []]
        if any(str(n or "").lower() == "icecore.notified" for n in names):
            return "icecore-error forbidden_event: icecore.notified is written by ICECORE_NOTIFIED only"
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
  {%- set ack -%}
CREATE OR REPLACE PROCEDURE {{ icecore._icecore_procedure('ICECORE_ACK', schema) }}(incident_key STRING)
COPY GRANTS
RETURNS STRING
LANGUAGE SQL
COMMENT = 'icecore: acknowledges an open incident of ALERTS_FEED, recording the caller (UC6)'
EXECUTE AS OWNER
AS $$
DECLARE
  who STRING DEFAULT CURRENT_USER();
  n INTEGER DEFAULT 0;
BEGIN
  SELECT COUNT(*) INTO :n FROM {{ icecore._icecore_procedure('ALERTS_FEED', schema) }}
    WHERE incident_key = :incident_key AND acknowledged_at IS NULL;
  IF (n = 0) THEN
    RETURN 'icecore-error not_an_open_incident: ' || COALESCE(:incident_key, 'null') || ' is not a current unacknowledged incident of ALERTS_FEED';
  END IF;
  SYSTEM$ADD_EVENT('icecore.ack', OBJECT_CONSTRUCT('icecore.ack.incident_key', :incident_key, 'icecore.ack.by', :who));
  RETURN 'ok 1';
END;
$$
  {%- endset -%}
  {%- set notified -%}
CREATE OR REPLACE PROCEDURE {{ icecore._icecore_procedure('ICECORE_NOTIFIED', schema) }}(incident_key STRING, channel STRING)
COPY GRANTS
RETURNS STRING
LANGUAGE SQL
COMMENT = 'icecore: records that an alert notified an incident on a channel (UC6); granted to the alerts owner only'
EXECUTE AS OWNER
AS $$
BEGIN
  IF (NOT REGEXP_LIKE(COALESCE(:incident_key, ''), '[0-9a-f]{32}')) THEN
    RETURN 'icecore-error invalid_incident_key: an incident key is 32 hex characters';
  END IF;
  IF (NOT REGEXP_LIKE(COALESCE(:channel, ''), '[A-Za-z][A-Za-z0-9_]{0,39}')) THEN
    RETURN 'icecore-error invalid_channel: a channel is a plain name';
  END IF;
  SYSTEM$ADD_EVENT('icecore.notified', OBJECT_CONSTRUCT('icecore.notified.incident_key', :incident_key,
      'icecore.notified.channel', :channel, 'icecore.notified.by', CURRENT_USER()));
  RETURN 'ok 1';
END;
$$
  {%- endset -%}
  {{ return([emit | trim, emit_run | trim, ack | trim, notified | trim]) }}
{% endmacro %}
