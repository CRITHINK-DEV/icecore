{#- UC6 silence detection (spec sections 5 and 7): runs that never happened.

    render_silence_task_sql(entries, schema, source) renders the BODY of the Snowflake task that icecore.create_silence_task
    creates: one Snowflake Scripting block, run as `EXECUTE IMMEDIATE $$ ... $$`. The SLA entries (collect_sla_config)
    are embedded as a VALUES list, so the task reads no dbt graph: change meta.sla and run setup again to refresh it.

    Rule, per entry with meta.sla (expected_every, grace; the default grace is collect_sla_config's, 1 minute from S3):
      last_success_at = the latest recorded_at of the node's rows in node_executions with status success or pass;
      expected_at     = last_success_at + expected_every;
      silent          = Snowflake's now > expected_at + grace.
    recorded_at is the TIMESTAMP of the result record, Snowflake's clock, so the comparison uses one clock and the
    grace also absorbs the ingestion lag (seconds, S3). A node with no successful row yet has no last_success_at and no
    verdict: the task cannot know when a run was expected.

    A verdict is a span event `icecore.silence`, written by a CALL of ICECORE_EMIT from the task body (never an INSERT;
    GATE1.md): dbt.node.unique_id and icecore.silence.expected_at / last_success_at (ISO-8601 UTC, to the second),
    expected_every_minutes, grace_minutes, channel, severity, at level warn. One verdict per missed run: a node already
    in silence_events with the same unique_id and expected_at gets none, so a task running every few minutes does not
    repeat itself once the first verdict is readable (it is not before it lands, seconds after the CALL). At most 100
    verdicts per run (the ICECORE_EMIT limit); the rest come with the next run. An error returns `icecore-error ...`
    instead of failing the task.

    `source` is the relation read as node_executions (default the obs schema's NODE_EXECUTIONS view). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro render_silence_task_sql(entries, schema=none, source=none) %}
  {%- set scheduled = [] -%}
  {%- for e in entries or [] -%}
    {%- if e['expected_every_minutes'] is not none -%}{%- do scheduled.append(e) -%}{%- endif -%}
  {%- endfor -%}
  {%- if scheduled | length == 0 -%}
    {{ exceptions.raise_compiler_error('icecore: render_silence_task_sql needs at least one entry with meta.sla') }}
  {%- endif -%}
  {%- set lit = icecore._icecore_sql_literal -%}
  {%- set source = source or icecore._icecore_procedure('NODE_EXECUTIONS', schema) -%}
  {%- set silence = icecore._icecore_procedure('SILENCE_EVENTS', schema) -%}
  {%- set emit = icecore._icecore_procedure('ICECORE_EMIT', schema) -%}
  {%- set values = [] -%}
  {%- for e in scheduled -%}
    {%- do values.append('(' ~ lit(e['unique_id']) ~ ', ' ~ (e['expected_every_minutes'] | int) ~ ', ' ~ (e['grace_minutes'] | int)
        ~ ', ' ~ ('null' if e['channel'] is none else lit(e['channel'])) ~ ', ' ~ lit(e['severity'] or 'warn') ~ ')') -%}
  {%- endfor -%}
  {%- set now = "convert_timezone('UTC', current_timestamp())::timestamp_ntz" -%}
  {%- set iso = "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'" -%}
  {%- set sql -%}
DECLARE
  events ARRAY;
  res STRING;
BEGIN
  events := (
    WITH sla (unique_id, every_min, grace_min, channel, severity) AS (
      SELECT * FROM VALUES
        {{ values | join(',\n        ') }}
    ),
    last_success AS (
      SELECT unique_id, MAX(recorded_at) AS last_success_at
      FROM {{ source }}
      WHERE status IN ('success', 'pass')
      GROUP BY unique_id
    ),
    due AS (
      SELECT sla.*, last_success.last_success_at,
             DATE_TRUNC('second', DATEADD(minute, sla.every_min, last_success.last_success_at)) AS expected_at
      FROM sla JOIN last_success ON last_success.unique_id = sla.unique_id
      WHERE {{ now }} > DATEADD(minute, sla.every_min + sla.grace_min, last_success.last_success_at)
    ),
    new_verdicts AS (
      SELECT due.* FROM due
      WHERE NOT EXISTS (
        SELECT 1 FROM {{ silence }} s WHERE s.unique_id = due.unique_id AND s.expected_at = due.expected_at)
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
  CALL {{ emit }}(:events) INTO :res;
  RETURN res;
EXCEPTION
  WHEN OTHER THEN RETURN 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
END;
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
