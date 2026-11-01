{#- UC6 create_silence_task: creates the Snowflake task ICECORE_SILENCE in the obs schema (setup step `create_tasks`).

    The task body is render_silence_task_sql(sla entries). Entries come from collect_sla_config() (the dbt graph) or
    the `entries` argument. Without any meta.sla nothing is created and a line says so.

    CREATE OR REPLACE TASK (a task has no COPY GRANTS: a re-run resets the task's grants and leaves it suspended).
    The task is created SUSPENDED, so creating it never starts spending credits without consent; var
    icecore.silence_task_resume: true resumes it. Resuming and EXECUTE TASK need the EXECUTE TASK privilege on the
    account: when the resume fails, the grant is printed for an admin (and when the role cannot create the task,
    the CREATE TASK grant).
    Vars (package defaults in dbt_project.yml): silence_task_schedule ('5 MINUTE'), silence_task_warehouse (default the
    target's warehouse; none runs the task serverless), silence_task_resume (false). Never an INSERT: the task writes
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
  {%- set resume = cfg.get('silence_task_resume', false) if resume is none else resume -%}
  {%- set task = icecore._icecore_procedure('ICECORE_SILENCE', schema) -%}
  {%- set body = icecore.render_silence_task_sql(scheduled, schema, source) -%}
  {%- set ddl = icecore._icecore_silence_task_ddl(task, body, warehouse, schedule) -%}
  {%- set status = icecore._icecore_guarded_ddl(ddl) -%}
  {%- do out.update({'task': task, 'status': status}) -%}
  {%- if status != 'ok' -%}
    {%- do out['printed'].append('GRANT CREATE TASK ON SCHEMA ' ~ icecore.obs_schema(schema) ~ ' TO ROLE ' ~ target.role) -%}
    {%- do log('icecore: warning: could not create ' ~ task ~ ' (' ~ status ~ '); an admin must run: ' ~ out['printed'][-1], info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- if resume is sameas true or (resume | string | lower) == 'true' -%}
    {%- set resumed = icecore._icecore_guarded_ddl('ALTER TASK ' ~ task ~ ' RESUME') -%}
    {%- if resumed == 'ok' -%}
      {%- do out.update({'resumed': true}) -%}
    {%- else -%}
      {%- do out['printed'].append('GRANT EXECUTE TASK ON ACCOUNT TO ROLE ' ~ target.role) -%}
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
      ~ '\n  SCHEDULE = ' ~ icecore._icecore_sql_literal(schedule)
      ~ "\n  COMMENT = 'icecore: detects runs that did not happen (meta.sla)'"
      ~ '\nAS\nEXECUTE IMMEDIATE $$\n' ~ body ~ '\n$$') }}
{% endmacro %}
