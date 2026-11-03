{#- UC6 create_silence_task: creates the Snowflake task ICECORE_SILENCE in the obs schema (setup step `create_tasks`).

    Creates the procedure ICECORE_SILENCE_CHECK(sla VARIANT) and the task, whose body is render_silence_task_sql(sla entries): a
    CALL carrying the SLA as one JSON literal (no SLA value is ever part of executed code). The procedure takes no name
    from its caller (the relations it reads and the emitter it calls are fixed, validated, in its DDL), and runs with
    its owner's rights: grant USAGE on it to nobody but the task's owner; the dbt role never needs it. `source` is the
    name of another view of the obs schema to read as node_executions (tests only). Entries come from
    collect_sla_config() (the dbt graph) or the `entries` argument. Without any meta.sla nothing is created and a line
    says so.

    CREATE OR REPLACE TASK (a task has no COPY GRANTS: a re-run resets the task's grants) leaves the task suspended;
    setup resumes it again when its var asks for it. A task that was running and whose var now says false stays
    suspended, and a line says so (the same as alerts).
    The task is created SUSPENDED, so creating it never starts spending credits without consent; var
    icecore.silence_task_resume: true resumes it (when it is not set, the var icecore.alerts decides; false keeps it
    suspended even with alerts: true). A resumed task uses warehouse time on every schedule period. Resuming and EXECUTE TASK need the EXECUTE TASK privilege on the
    account: when the resume fails, the grant is printed for an admin (and when the role cannot create the task,
    the CREATE TASK grant).
    Vars (package defaults in dbt_project.yml): silence_task_schedule ('5 MINUTE'), silence_task_warehouse (default the
    target's warehouse; none runs the task serverless), silence_task_resume (none: follows `alerts`). Never an INSERT: the task writes
    through a CALL of ICECORE_EMIT. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {task, status, resumed, printed}: status is ok, skipped (no meta.sla) or the guarded error; printed holds
    the statements an admin must run. -#}
{% macro create_silence_task(schema=none, entries=none, source=none, warehouse=none, schedule=none, resume=none) %}
  {%- set out = {'task': none, 'status': 'skipped', 'resumed': false, 'printed': []} -%}
  {%- if not execute -%}{{ return(out) }}{%- endif -%}
  {%- set cfg = var('icecore', {}) -%}
  {%- set entries = icecore.collect_sla_config() if entries is none else entries -%}
  {%- set scheduled = [] -%}
  {%- for e in entries -%}
    {%- if e['expected_every_minutes'] is not none -%}{%- do scheduled.append(e) -%}{%- endif -%}
  {%- endfor -%}
  {%- if scheduled | length == 0 -%}
    {%- do log('icecore: silence task not created: no model declares meta.sla', info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set warehouse = warehouse or cfg.get('silence_task_warehouse') or target.warehouse -%}
  {%- set schedule = schedule or cfg.get('silence_task_schedule', '5 MINUTE') -%}
  {%- set resume = icecore._icecore_resume_flag('silence_task_resume') if resume is none else resume -%}
  {%- set task = icecore._icecore_procedure('ICECORE_SILENCE', schema) -%}
  {#- the state of a task created earlier: a re-run keeps it (CREATE OR REPLACE would leave it suspended) -#}
  {%- set before = run_query('SHOW TASKS LIKE ' ~ icecore._sql('ICECORE_SILENCE', 'string') ~ ' IN SCHEMA ' ~ icecore.obs_schema(schema)).rows -%}
  {%- set was_started = (before | length) > 0 and (before[0]['state'] | string | lower) == 'started' -%}
  {#- the procedure first: static code, no SLA value. The task body is a CALL carrying the SLA as one JSON literal. -#}
  {#- Snowflake keys procedures by signature: CREATE OR REPLACE of (VARIANT) leaves an older (VARIANT, VARCHAR, VARCHAR, VARCHAR)
      overload in place, the one that took relation names from its caller. Drop it first. -#}
  {%- do icecore._icecore_guarded_ddl('DROP PROCEDURE IF EXISTS ' ~ icecore._icecore_procedure('ICECORE_SILENCE_CHECK', schema) ~ '(VARIANT, VARCHAR, VARCHAR, VARCHAR)') -%}
  {%- set made = icecore._icecore_guarded_ddl(icecore._icecore_silence_procedure_ddl(schema, source or 'NODE_EXECUTIONS')) -%}
  {%- set status = made -%}
  {%- if made == 'ok' -%}
    {%- set body = icecore.render_silence_task_sql(scheduled, schema) -%}
    {%- set status = icecore._icecore_guarded_ddl(icecore._icecore_silence_task_ddl(task, body, warehouse, schedule)) -%}
  {%- endif -%}
  {%- do out.update({'task': task, 'status': status}) -%}
  {%- if status != 'ok' -%}
    {%- do out['printed'].append('GRANT CREATE TASK, CREATE PROCEDURE ON SCHEMA ' ~ icecore.obs_schema(schema) ~ ' TO ROLE ' ~ icecore._icecore_identifier(target.role)) -%}
    {%- do log('icecore: warning: could not create ' ~ task ~ ' (' ~ status ~ '); an admin must run: ' ~ out['printed'][-1], info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set want_resume = resume is sameas true or (resume | string | lower) == 'true' -%}
  {%- if was_started and not want_resume -%}
    {%- do log('icecore: ' ~ task ~ ' was resumed before this run: it was recreated and stays suspended because its var says false', info=True) -%}
  {%- endif -%}
  {%- if want_resume -%}
    {%- set resumed = icecore._icecore_guarded_ddl('ALTER TASK ' ~ task ~ ' RESUME') -%}
    {%- if resumed == 'ok' -%}
      {%- do out.update({'resumed': true}) -%}
    {%- else -%}
      {%- do out['printed'].append('GRANT EXECUTE TASK ON ACCOUNT TO ROLE ' ~ icecore._icecore_identifier(target.role)) -%}
      {%- do log('icecore: warning: could not resume ' ~ task ~ ' (' ~ resumed ~ '); it stays suspended. An admin must run: '
          ~ out['printed'][-1], info=True) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{% macro _icecore_silence_task_ddl(task, body, warehouse, schedule) %}
  {{ return('CREATE OR REPLACE TASK ' ~ task
      ~ ('\n  WAREHOUSE = ' ~ icecore._icecore_identifier(warehouse) if warehouse else '')
      ~ '\n  SCHEDULE = ' ~ icecore._sql(schedule, 'string')
      ~ "\n  COMMENT = 'icecore: detects runs that did not happen (meta.sla)'"
      ~ '\nAS\n' ~ body) }}
{% endmacro %}
