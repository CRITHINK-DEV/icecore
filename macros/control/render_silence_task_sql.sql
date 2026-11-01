{#- UC6 silence detection (spec sections 5 and 7): runs that never happened.

    Two pieces, so that no SLA value is ever part of executed code:
      - the procedure ICECORE_SILENCE_CHECK(sla VARIANT), created by create_silence_task from
        _icecore_silence_procedure_ddl: a Snowflake Scripting body that holds no dbt value. It takes ONLY the SLA JSON: the
        fully qualified names of NODE_EXECUTIONS, SILENCE_EVENTS and ICECORE_EMIT are fixed in its DDL when it is created,
        each part validated ([A-Za-z0-9_]+, whole string) and double-quoted by _icecore_silence_name. It runs with its
        owner's rights, so it must not take a name from its caller (a caller could point it at any object the owner can
        read or call): nobody but the task's owner is granted USAGE on it, and the dbt role never needs it;
      - the task body, which render_silence_task_sql(entries, schema) returns: one CALL of that procedure.
        The SLA entries (collect_sla_config; meta.sla values are user text) travel as ONE JSON string, PARSE_JSON('...'),
        a single-quoted literal whose only special characters, backslash and quote, are escaped; JSON escapes the
        rest. `$$`, `;`, newlines and quotes in a unique_id or a channel therefore stay data: they cannot end the literal,
        and the statement has no other text that depends on them. The SLA is embedded when setup runs: change meta.sla and
        run setup again to refresh the task.

    Rule, per entry with meta.sla (expected_every, grace; the default grace is collect_sla_config's, 1 minute from S3):
      last_success_at = the latest recorded_at of the node's rows in node_executions with status success or pass;
      expected_at     = last_success_at + expected_every;
      silent          = Snowflake's now > expected_at + grace.
    recorded_at is the TIMESTAMP of the result record, Snowflake's clock, so the comparison uses one clock and the
    grace also absorbs the ingestion lag (seconds, S3). A node with no successful row yet has no last_success_at and no
    verdict: the task cannot know when a run was expected.

    A verdict is a span event `icecore.silence`, written by a CALL of ICECORE_EMIT from the procedure (never an INSERT;
    GATE1.md): dbt.node.unique_id and icecore.silence.expected_at / last_success_at (ISO-8601 UTC, to the second),
    expected_every_minutes, grace_minutes, channel, severity, at level warn. One verdict per missed run: a node already
    in silence_events with the same unique_id and expected_at gets none, so a task running every few minutes does not
    repeat itself once the first verdict is readable (it is not before it lands, seconds after the CALL). At most 100
    verdicts per run (the ICECORE_EMIT limit); the rest come with the next run. A failure RAISES, so the task history
    shows the error: a failed read, or an ICECORE_EMIT answer that starts with `icecore-error`.

    The view read as node_executions is the obs schema's NODE_EXECUTIONS (a test names another view of that schema when it
    creates the procedure). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- The task body: a CALL of ICECORE_SILENCE_CHECK with the SLA entries as one JSON literal. -#}
{% macro render_silence_task_sql(entries, schema=none) %}
  {%- set scheduled = [] -%}
  {%- for e in entries or [] -%}
    {%- if e['expected_every_minutes'] is not none -%}
      {%- do scheduled.append({'unique_id': e['unique_id'], 'every_min': e['expected_every_minutes'] | int,
                               'grace_min': e['grace_minutes'] | int, 'channel': e['channel'],
                               'severity': e['severity'] or 'warn'}) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if scheduled | length == 0 -%}
    {{ exceptions.raise_compiler_error('icecore: render_silence_task_sql needs at least one entry with meta.sla') }}
  {%- endif -%}
  {{ return('CALL ' ~ icecore._icecore_procedure('ICECORE_SILENCE_CHECK', schema) ~ '(PARSE_JSON('
      ~ icecore._icecore_sql_literal(tojson(scheduled)) ~ '))') }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- DDL of ICECORE_SILENCE_CHECK. Static: the same text whatever the project declares. -#}
{#- A fully qualified, quoted name of the obs schema, validated: each part must be [A-Za-z0-9_]+ over the whole string.
    `view` is the object's name. Raises a compiler error otherwise, so nothing but a plain identifier reaches the DDL. -#}
{% macro _icecore_silence_name(view, schema=none) %}
  {%- set parts = [target.database | string | upper, icecore.obs_schema_name(schema), view | string | upper] -%}
  {%- set quoted = [] -%}
  {%- for part in parts -%}
    {%- if not modules.re.match('^[A-Za-z0-9_]+\\Z', part) -%}
      {{ exceptions.raise_compiler_error('icecore: ' ~ part ~ ' is not a plain identifier (letters, digits and underscores); the silence procedure only embeds those') }}
    {%- endif -%}
    {%- do quoted.append('"' ~ part ~ '"') -%}
  {%- endfor -%}
  {{ return(quoted | join('.')) }}
{% endmacro %}


{#- DDL of ICECORE_SILENCE_CHECK. The text depends on the schema (and the view name a test gives), never on the project's SLA. -#}
{% macro _icecore_silence_procedure_ddl(schema=none, source='NODE_EXECUTIONS') %}
  {%- set now = "convert_timezone('UTC', current_timestamp())::timestamp_ntz" -%}
  {%- set iso = "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'" -%}
  {%- set sql -%}
CREATE OR REPLACE PROCEDURE {{ icecore._icecore_procedure('ICECORE_SILENCE_CHECK', schema) }}(sla VARIANT)
COPY GRANTS
RETURNS VARCHAR
LANGUAGE SQL
COMMENT = 'icecore: writes icecore.silence span events for runs that did not happen (called by the ICECORE_SILENCE task)'
AS $$
DECLARE
  events ARRAY;
  res VARCHAR;
  emit_failed EXCEPTION (-20001, 'icecore: ICECORE_EMIT did not write the silence verdicts');
BEGIN
  events := (
    WITH sla_entries AS (
      SELECT f.value:unique_id::STRING AS unique_id, f.value:every_min::NUMBER AS every_min,
             f.value:grace_min::NUMBER AS grace_min, f.value:channel::STRING AS channel,
             f.value:severity::STRING AS severity
      FROM TABLE(FLATTEN(INPUT => :sla)) f
    ),
    last_success AS (
      SELECT unique_id, MAX(recorded_at) AS last_success_at
      FROM {{ icecore._icecore_silence_name(source, schema) }}
      WHERE status IN ('success', 'pass')
      GROUP BY unique_id
    ),
    due AS (
      SELECT s.*, l.last_success_at,
             DATE_TRUNC('second', DATEADD(minute, s.every_min, l.last_success_at)) AS expected_at
      FROM sla_entries s JOIN last_success l ON l.unique_id = s.unique_id
      WHERE {{ now }} > DATEADD(minute, s.every_min + s.grace_min, l.last_success_at)
    ),
    new_verdicts AS (
      SELECT due.* FROM due
      WHERE NOT EXISTS (
        SELECT 1 FROM {{ icecore._icecore_silence_name('SILENCE_EVENTS', schema) }} v WHERE v.unique_id = due.unique_id AND v.expected_at = due.expected_at)
      ORDER BY due.expected_at, due.unique_id
      LIMIT 100
    )
    SELECT COALESCE(ARRAY_AGG(OBJECT_CONSTRUCT_KEEP_NULL(
      'name', 'icecore.silence',
      'attrs', OBJECT_CONSTRUCT_KEEP_NULL(
        'dbt.node.unique_id', unique_id,
        'icecore.silence.expected_at', TO_VARCHAR(expected_at, {{ iso }}),
        'icecore.silence.last_success_at', TO_VARCHAR(DATE_TRUNC('second', last_success_at), {{ iso }}),
        'icecore.silence.expected_every_minutes', every_min,
        'icecore.silence.grace_minutes', grace_min,
        'icecore.silence.channel', channel,
        'icecore.silence.severity', severity,
        'icecore.schema_version', 1,
        'icecore.level', 'warn',
        'icecore.message', 'icecore: silence: ' || unique_id || ' expected at ' || TO_VARCHAR(expected_at, {{ iso }})
            || ', last success ' || TO_VARCHAR(DATE_TRUNC('second', last_success_at), {{ iso }})))), ARRAY_CONSTRUCT())
    FROM new_verdicts
  );
  IF (ARRAY_SIZE(events) = 0) THEN
    RETURN 'ok 0';
  END IF;
  CALL {{ icecore._icecore_silence_name('ICECORE_EMIT', schema) }}(:events) INTO :res;
  IF (STARTSWITH(res, 'icecore-error')) THEN
    RAISE emit_failed;
  END IF;
  RETURN res;
END;
$$
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
