{#-
  The write-path contract of Gate 1 option B (spikes/GATE1.md): the two persisted procedures every icecore record
  is written through, both EXECUTE AS OWNER, in <target.database>.<obs schema>, and the scoring procedure next to them.

  ICECORE_EMIT(events ARRAY)      SQL. One SPAN_EVENT per element {name, attrs} through SYSTEM$ADD_EVENT, named
                                  `icecore.<kind>` (SQL cannot set SCOPE, so it stays `global`). Refuses more than
                                  100 events per CALL: above 128 only the last 128 land (S2.md).
  ICECORE_EMIT_RUN(spans ARRAY)   Python 3.11, packages pinned (an unpinned opentelemetry-api resolved to 1.45.1 and
                                  failed to import, S1.md). Elements are spans {name, start_ns, end_ns, attrs,
                                  events: [{name, ts_ns, attrs}]} written with their explicit times, or logs
                                  {type: 'log', level, message, attrs}. SCOPE `icecore`.

  ICECORE_SCORE(model, series, points, prediction_interval, refresh_days)
                                  SQL, RETURNS VARIANT, EXECUTE AS OWNER. Scores points with one Cortex anomaly model
                                  in the owner's role, the role that trained it: Snowflake documents that models cannot be
                                  shared across roles, so the dbt role needs only USAGE on this procedure, never on a model.
                                  Reads the model's age with SHOW, refuses a missing or stale one, and returns
                                  {state, age_days, rows} (rows: DETECT_ANOMALIES output, latest first) or {error}.
                                  `points` is an ARRAY of {ts, y} (ts null: now, UTC) for the one `series`; the input is
                                  built from the array, never from caller SQL. It takes no database or schema argument: its
                                  own database and obs schema are constants fixed in the body when setup creates it (checked
                                  against [A-Za-z0-9_]), and `model` must be ICECORE_M_<16 hex>, so the owner's rights
                                  can reach only the models of its own schema (no confused deputy).

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
    {#- Render and validate every DDL first (_icecore_score_constant refuses a database or schema outside [A-Za-z0-9_]
        and stops setup), so nothing is dropped for names that fail the check. TODO(#75): use _sql(..., 'ident') for the names. -#}
    {%- set ddls = icecore._icecore_emit_procedures_ddl(schema) -%}
    {#- Snowflake keys a procedure by its signature: CREATE OR REPLACE of the shorter ICECORE_SCORE would leave an earlier
        signature (with database and schema arguments, a confused deputy) callable. Drop each retired one first, guarded,
        and say so when a drop did not work. -#}
    {%- for ddl in icecore._icecore_retired_score_drops(schema) -%}
      {%- set status = icecore._icecore_guarded_ddl(ddl) -%}
      {%- if status != 'ok' -%}
        {%- do log('icecore: warning: could not drop a retired ICECORE_SCORE signature: ' ~ status, info=True) -%}
      {%- endif -%}
    {%- endfor -%}
    {%- for ddl in ddls -%}
      {%- do run_query(ddl) -%}
    {%- endfor -%}
    {%- for name in ['ICECORE_EMIT', 'ICECORE_EMIT_RUN', 'ICECORE_SCORE'] -%}
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
  {%- set score -%}
CREATE OR REPLACE PROCEDURE {{ icecore._icecore_procedure('ICECORE_SCORE', schema) }}(model STRING, series STRING, points ARRAY, prediction_interval FLOAT, refresh_days FLOAT)
COPY GRANTS
RETURNS VARIANT
LANGUAGE SQL
COMMENT = 'icecore: scores points with a Cortex anomaly model in the owner role (models cannot be shared across roles)'
EXECUTE AS OWNER
AS $$
DECLARE
  db STRING DEFAULT '{{ icecore._icecore_score_constant(target.database) }}';
  sch STRING DEFAULT '{{ icecore._icecore_score_constant(icecore.obs_schema_name(schema)) }}';
  q STRING DEFAULT CHR(39);
  qq STRING DEFAULT CHR(39) || CHR(39);
  found INTEGER DEFAULT 0;
  age_s NUMBER DEFAULT 0;
  n INTEGER;
  ts_ntz TIMESTAMP_NTZ;
  y_f FLOAT;
  vals STRING DEFAULT '';
  series_hash STRING;
  input_sql STRING;
  rows_out VARIANT;
BEGIN
  -- No SQL text is built from a caller's string. The database and schema are this procedure's own constants; the model must be
  -- ICECORE_M_<16 hex> (checked before any use); the series key is hashed (the models are trained on the same hash); points are
  -- typed (a timestamp and a finite float) before they are written into the input; the interval is a float.
  IF (model IS NULL OR NOT REGEXP_LIKE(model, 'ICECORE_M_[0-9A-F]{16}')) THEN
    RETURN OBJECT_CONSTRUCT('error', 'model must be ICECORE_M_ and 16 upper-case hex digits');
  END IF;
  IF (prediction_interval IS NULL OR prediction_interval <= 0 OR prediction_interval >= 1) THEN
    RETURN OBJECT_CONSTRUCT('error', 'prediction_interval must be between 0 and 1');
  END IF;
  n := COALESCE(ARRAY_SIZE(points), 0);
  IF (n = 0 OR n > 1000 OR series IS NULL) THEN
    RETURN OBJECT_CONSTRUCT('error', 'a series and 1 to 1000 points are required');
  END IF;
  EXECUTE IMMEDIATE 'SHOW SNOWFLAKE.ML.ANOMALY_DETECTION LIKE ' || q || model || q || ' IN SCHEMA "' || db || '"."' || sch || '"';
  SELECT COUNT(*), COALESCE(MAX(DATEDIFF('second', "created_on", CURRENT_TIMESTAMP())), 0) INTO :found, :age_s
    FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())) WHERE "name" = :model;
  IF (found = 0) THEN
    RETURN OBJECT_CONSTRUCT('state', 'missing');
  END IF;
  IF (refresh_days IS NOT NULL AND age_s / 86400 > refresh_days) THEN
    RETURN OBJECT_CONSTRUCT('state', 'stale', 'age_days', age_s / 86400);
  END IF;
  series_hash := MD5(series);
  FOR i IN 0 TO n - 1 DO
    ts_ntz := COALESCE(TRY_TO_TIMESTAMP_NTZ(points[i]:ts::STRING), CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ);
    y_f := TRY_TO_DOUBLE(points[i]:y::STRING);
    IF (y_f IS NULL OR NOT REGEXP_LIKE(TO_VARCHAR(y_f), '-?[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?')) THEN
      RETURN OBJECT_CONSTRUCT('error', 'a point has no finite numeric y');
    END IF;
    vals := vals || IFF(i = 0, '', ', ') || '(' || qq || TO_VARCHAR(ts_ntz, 'YYYY-MM-DD HH24:MI:SS.FF3') || qq || ', ' || TO_VARCHAR(y_f) || ')';
  END FOR;
  input_sql := 'select ' || qq || series_hash || qq || ' as series, column1::timestamp_ntz as ts, column2::float as y from values ' || vals;
  EXECUTE IMMEDIATE 'CALL "' || db || '"."' || sch || '"."' || model || '"!DETECT_ANOMALIES('
    || 'INPUT_DATA => SYSTEM$QUERY_REFERENCE(' || q || input_sql || q || '), '
    || 'SERIES_COLNAME => ' || q || 'SERIES' || q || ', TIMESTAMP_COLNAME => ' || q || 'TS' || q || ', TARGET_COLNAME => ' || q || 'Y' || q || ', '
    || 'CONFIG_OBJECT => {' || q || 'prediction_interval' || q || ': ' || TO_VARCHAR(prediction_interval) || '})';
  SELECT COALESCE(ARRAY_AGG(OBJECT_CONSTRUCT(*)) WITHIN GROUP (ORDER BY ts DESC), ARRAY_CONSTRUCT()) INTO :rows_out FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
  RETURN OBJECT_CONSTRUCT('state', 'ok', 'age_days', age_s / 86400, 'rows', rows_out);
EXCEPTION
  WHEN OTHER THEN RETURN OBJECT_CONSTRUCT('error', SQLCODE || ': ' || SQLERRM);
END;
$$
  {%- endset -%}
  {{ return([emit | trim, emit_run | trim, score | trim]) }}
{% endmacro %}


{#- A database or schema name that goes into an owner's-rights body or task text as a constant (ICECORE_SCORE, the refresh task):
    only [A-Za-z0-9_] on the name as given (so no quote, no $, no control character, and no upper-casing that changes it), whole
    string (\Z), upper-cased; anything else stops the macro. TODO(#75): replace by _sql(..., 'ident') once icecore._sql merges. -#}
{% macro _icecore_score_constant(name) %}
  {%- if not modules.re.match('^[A-Za-z0-9_]+\\Z', name | string) -%}
    {{ exceptions.raise_compiler_error("icecore: the database and the obs schema must be plain identifiers ([A-Za-z0-9_]) to be written into an owner's-rights procedure or task, got '" ~ name ~ "'") }}
  {%- endif -%}
  {{ return(name | string | upper) }}
{% endmacro %}


{#- Every ICECORE_SCORE signature shipped before the current one, as DROP statements. Add a line here whenever the signature changes. -#}
{% macro _icecore_retired_score_drops(schema=none) %}
  {%- set retired = ['STRING, STRING, STRING, STRING, ARRAY, FLOAT, FLOAT'] -%}
  {%- set drops = [] -%}
  {%- for signature in retired -%}
    {%- do drops.append('DROP PROCEDURE IF EXISTS ' ~ icecore._icecore_procedure('ICECORE_SCORE', schema) ~ '(' ~ signature ~ ')') -%}
  {%- endfor -%}
  {{ return(drops) }}
{% endmacro %}
