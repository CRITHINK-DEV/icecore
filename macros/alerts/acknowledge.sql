{#- UC6 acknowledge: `dbt run-operation icecore.acknowledge --args '{incident_key: <key>}'`. Calls the ICECORE_ACK
    procedure (macros/setup/procedures.sql), which refuses a key that is not a current unacknowledged incident of
    alerts_feed and records who acknowledged from the caller's own identity (CURRENT_USER), so there is no `by`
    argument and nothing here writes a record itself (never an INSERT, and ICECORE_EMIT refuses `icecore.ack`).
    The alerts_feed row of that key then shows acknowledged_at and acknowledged_by; a recurrence in a later window
    has a new key and alerts again. The app calls the same macro. Returns the procedure's status, `ok 1`, or
    `icecore-error ...` (logged as a warning). A key is 32 hex characters. -#}
{% macro acknowledge(incident_key) %}
  {%- set key = (incident_key or '') | string | trim -%}
  {%- if not modules.re.match('^[0-9a-f]{32}$', key) -%}
    {{ exceptions.raise_compiler_error('icecore.acknowledge needs an incident_key, 32 hex characters (a value of alerts_feed.incident_key)') }}
  {%- endif -%}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set sql -%}
EXECUTE IMMEDIATE $$
DECLARE
  k STRING DEFAULT (PARSE_JSON({{ icecore._sql(tojson(key), 'string') }}))::STRING;
  r STRING;
BEGIN
  CALL {{ icecore._icecore_procedure('ICECORE_ACK') }}(:k) INTO :r;
  RETURN r;
EXCEPTION
  WHEN OTHER THEN RETURN 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
END;
$$
  {%- endset -%}
  {%- set status = run_query(sql | trim).columns[0].values()[0] | string -%}
  {%- if status.startswith('icecore-error') -%}
    {%- do log('icecore: warning: incident not acknowledged: ' ~ status, info=True) -%}
  {%- else -%}
    {%- do log('icecore: acknowledged incident ' ~ key, info=True) -%}
  {%- endif -%}
  {{ return(status) }}
{% endmacro %}
