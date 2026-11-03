{#- UC3 Cortex model refresh (spec sections 5 and 6): a Snowflake TASK retrains each monitored table's
    SNOWFLAKE.ML.ANOMALY_DETECTION model from the metric history. Models are never trained at test time.

    This file is the only place icecore creates SNOWFLAKE.ML objects (test_engine_contract allows it by name).

    One model per table (the `relation` of the METRICS view), trained on all that table's series. The training window and
    rules are get_metric_history's, applied to every series of the table: observed_at after now - lookback_days (30)
    and up to now - history delay (var icecore.history_delay_minutes, 1), UTC; a series with fewer than min_points
    (14, the engine's own history rule) points in that window is left out, and a table with no such series gets no model.
    A series is keyed by cortex_series_key(metric, column, dimension) and a table's model is named by
    cortex_model_name(relation): the dispatcher's `model` and `series` arguments. The task body is one Scripting block
    (a cursor over the tables, one guarded CREATE OR REPLACE per table; a failure on one table is reported and does not
    stop the others) that reads only the metrics view. It returns 'ok <n> models, <k> skipped' or 'icecore-error ...' and also sets that text as the task's return value
    (SYSTEM$SET_RETURN_VALUE), since a task's RETURN is discarded. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Name of a table's model, the hash of its relation, never its text: ICECORE_M_<first 16 hex of md5(relation), upper>, the bare name
    ICECORE_SCORE accepts (it must match ICECORE_M_ and 16 upper-case hex digits) in the obs schema. `relation` is the quoted
    "DB"."SCHEMA"."NAME" the metrics view carries, or a dbt relation. The task computes the same name in SQL and records the mapping
    on an `icecore.cortex_model` event. -#}
{% macro cortex_model_name(relation, schema=none) %}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {{ return('ICECORE_M_' ~ (local_md5(name)[:16] | upper)) }}
{% endmacro %}


{#- The series key score takes: metric|column|dimension, with ~ for a missing column or dimension. A model is trained on
    its MD5 (the task and ICECORE_SCORE both hash in SQL), so the key never reaches SQL as text. A metric with a | in its
    name could collide with another series; harmless to safety. -#}
{% macro cortex_series_key(metric, column=none, dimension=none) %}
  {{ return((metric | string) ~ '|' ~ ('~' if column is none else column | string) ~ '|' ~ ('~' if dimension is none else dimension | string)) }}
{% endmacro %}


{#- The task body: a Scripting block that reads the obs schema's METRICS view. It is constant text: the database and the
    schema are checked against [A-Za-z0-9_] (_icecore_refresh_names, which stops with a compile error otherwise; quoted by icecore._sql(..., 'ident')) before they are written, quoted, into it, and there is no `source` argument.

    No SQL text is built from a record value. `relation` comes from event records the dbt role can write, so it is only
    ever (1) checked against the strict quoted-identifier shape "DB"."SCHEMA"."NAME" (anything else is skipped and counted),
    (2) hashed (MD5) to name the model and to filter the training rows, and (3) passed as a bound value inside
    OBJECT_CONSTRUCT to the mapping event. The statements that are built from text contain only this macro's own constants,
    hex digests and integers; the series key is hashed in SQL (MD5), never pasted. The one string literal in the body that
    carries a value, the digest, is a hex string. -#}
{% macro render_refresh_cortex_models_sql(schema=none, lookback_days=30, delay_minutes=none, min_points=14) %}
  {%- if lookback_days is not integer or lookback_days < 1 -%}
    {{ exceptions.raise_compiler_error('icecore: lookback_days must be an integer of at least 1, got ' ~ lookback_days) }}
  {%- endif -%}
  {%- if min_points is not integer or min_points < 12 -%}
    {{ exceptions.raise_compiler_error('icecore: min_points must be an integer of at least 12 (Cortex needs 12 points per series), got ' ~ min_points) }}
  {%- endif -%}
  {%- set delay = icecore._icecore_history_delay(delay_minutes) -%}
  {%- if delay is not integer or delay < 0 -%}
    {{ exceptions.raise_compiler_error('icecore: history delay must be an integer of at least 0 minutes, got ' ~ delay) }}
  {%- endif -%}
  {%- set names = icecore._icecore_refresh_names(schema) -%}
  {%- set source = names['metrics'] -%}
  {%- set emit = names['emit'] -%}
  {%- set qualified = names['qualified'] -%}
  {%- set version = icecore._icecore_package_version() | string -%}
  {%- if not modules.re.match('^[A-Za-z0-9._+-]+\\Z', version) -%}
    {{ exceptions.raise_compiler_error("icecore: the package version must be letters, digits and . _ + - to be written into the task body, got '" ~ version ~ "'") }}
  {%- endif -%}
  {%- set window = 'value is not null and observed_at > dateadd(day, -' ~ lookback_days ~ ', sysdate()) and observed_at <= dateadd(minute, -' ~ delay ~ ', sysdate())' -%}
  {#- The training query, in two constant pieces around the digest. Each goes through icecore._sql, so even a `$` in a name
      is written \x24 and nothing in the body can end it. -#}
  {%- set train_head = 'select md5(nvl(metric, chr(126)) || chr(124) || nvl("COLUMN", chr(126)) || chr(124) || nvl(dimension, chr(126))) as series, '
      ~ 'observed_at as ts, value as y from (select metric, "COLUMN", dimension, observed_at, value, '
      ~ 'count(*) over (partition by metric, "COLUMN", dimension) as n from ' ~ source ~ ' where md5(relation) = ' -%}
  {%- set train_tail = ' and ' ~ window ~ ') where n >= ' ~ min_points -%}
  {%- set sql -%}
DECLARE
  q STRING DEFAULT CHR(39);
  qq STRING DEFAULT CHR(39) || CHR(39);
  created ARRAY DEFAULT ARRAY_CONSTRUCT();
  failed ARRAY DEFAULT ARRAY_CONSTRUCT();
  events ARRAY DEFAULT ARRAY_CONSTRUCT();
  skipped INTEGER DEFAULT 0;
  rels RESULTSET DEFAULT (
    SELECT relation FROM (
      SELECT relation, metric, "COLUMN", dimension, COUNT(*) AS n FROM {{ source }}
      WHERE {{ window }}
      GROUP BY relation, metric, "COLUMN", dimension)
    WHERE n >= {{ min_points }} GROUP BY relation ORDER BY relation LIMIT 100);
  digest STRING;
  model_id STRING;
  train STRING;
  emitted STRING;
  msg STRING;
  ret STRING;  -- the return-value call takes a variable, not an expression
BEGIN
  FOR r IN rels DO
    -- a relation is a record value: only the strict shape "DB"."SCHEMA"."NAME" (no quote inside a part) goes on
    IF (r.relation IS NULL OR NOT REGEXP_LIKE(r.relation, '"[^"]+"\\."[^"]+"\\."[^"]+"')) THEN
      skipped := skipped + 1;
    ELSE
      BEGIN
        digest := MD5(r.relation);
        model_id := 'ICECORE_M_' || UPPER(LEFT(digest, 16));
        train := {{ icecore._sql(train_head, 'string') }} || qq || digest || qq || {{ icecore._sql(train_tail, 'string') }};
        EXECUTE IMMEDIATE {{ icecore._sql('CREATE OR REPLACE SNOWFLAKE.ML.ANOMALY_DETECTION ' ~ qualified ~ '.', 'string') }} || CHR(34) || model_id || CHR(34)
          || '(INPUT_DATA => SYSTEM$QUERY_REFERENCE(' || q || train || q || '), SERIES_COLNAME => ' || q || 'SERIES' || q
          || ', TIMESTAMP_COLNAME => ' || q || 'TS' || q || ', TARGET_COLNAME => ' || q || 'Y' || q || ', LABEL_COLNAME => ' || q || q || ')';
        created := ARRAY_APPEND(created, model_id);
        -- the mapping relation -> model, a span event with bound values (the relation is data, never SQL)
        events := ARRAY_APPEND(events, OBJECT_CONSTRUCT_KEEP_NULL(
          'name', 'icecore.cortex_model',
          'attrs', OBJECT_CONSTRUCT_KEEP_NULL(
            'dbt.node.relation', r.relation,
            'icecore.cortex.model', model_id,
            'icecore.schema_version', 1,
            'icecore.version', {{ icecore._sql(version, 'string') }},
            'icecore.level', 'info',
            'icecore.message', 'icecore: Cortex model ' || model_id || ' refreshed')));
      EXCEPTION
        WHEN OTHER THEN failed := ARRAY_APPEND(failed, model_id || ': ' || SQLCODE || ': ' || SQLERRM);
      END;
    END IF;
  END FOR;
  IF (ARRAY_SIZE(events) > 0) THEN
    BEGIN
      CALL {{ emit }}(:events) INTO :emitted;
    EXCEPTION
      WHEN OTHER THEN failed := ARRAY_APPEND(failed, 'mapping event: ' || SQLCODE || ': ' || SQLERRM);
    END;
  END IF;
  IF (ARRAY_SIZE(failed) > 0) THEN
    msg := 'icecore-error ' || ARRAY_SIZE(created) || ' models, ' || skipped || ' skipped, ' || ARRAY_SIZE(failed) || ' failed: ' || ARRAY_TO_STRING(failed, '; ');
  ELSE
    msg := 'ok ' || ARRAY_SIZE(created) || ' models, ' || skipped || ' skipped';
  END IF;
  -- a task keeps no RETURN: the result is its return value, read from TASK_HISTORY (RETURN_VALUE) or by a child task
  BEGIN
    ret := LEFT(msg, 9000);
    CALL SYSTEM$SET_RETURN_VALUE(:ret);
  EXCEPTION
    WHEN OTHER THEN NULL;  -- run outside a task (a test, a manual run): the RETURN below still carries it
  END;
  RETURN msg;
EXCEPTION
  WHEN OTHER THEN
    msg := 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
    BEGIN
      ret := LEFT(msg, 9000);
      CALL SYSTEM$SET_RETURN_VALUE(:ret);
    EXCEPTION
      WHEN OTHER THEN NULL;
    END;
    RETURN msg;
END;
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Creates the task ICECORE_REFRESH_CORTEX in the obs schema (setup's create_tasks step), only when Cortex is available
    (`capabilities`, a detect_capabilities result; default detects, without recording). Returns {task, status, resumed,
    printed}: status ok, skipped (Cortex off) or the guarded error. A new task is created SUSPENDED: var
    icecore.cortex_refresh_task_resume: true resumes it. A task an admin had resumed stays resumed: CREATE OR REPLACE
    suspends it, so SHOW TASKS runs first and the task is resumed again when it was started. Schedule: var
    icecore.cortex_refresh_days (default 7, whole days from 1 to 8: Snowflake refuses a schedule above 11520 minutes) as
    minutes. Warehouse: var cortex_refresh_task_warehouse, else the target's. The role that runs dbt (argument `dbt_role`,
    else var icecore.dbt_role) is named in the printed USAGE grant on ICECORE_SCORE; models are never granted. `printed`
    holds what an admin must run. -#}
{% macro create_refresh_cortex_task(schema=none, capabilities=none, warehouse=none, resume=none, days=none, min_points=14, dbt_role=none) %}
  {%- set out = {'task': none, 'status': 'skipped', 'resumed': false, 'printed': []} -%}
  {%- if not execute -%}{{ return(out) }}{%- endif -%}
  {%- set cfg = var('icecore', {}) -%}
  {%- set capabilities = icecore.detect_capabilities(record=false) if capabilities is none else capabilities -%}
  {%- if capabilities.get('cortex') is not sameas true -%}
    {%- do log('icecore: Cortex refresh task not created: Cortex is not available (' ~ capabilities.get('cortex_reason') ~ ')', info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set days = cfg.get('cortex_refresh_days', 7) if days is none else days -%}
  {%- if days is not integer or days is sameas true or days is sameas false or days < 1 or days > 8 -%}
    {{ exceptions.raise_compiler_error("icecore: cortex_refresh_days must be a whole number of days from 1 to 8 to schedule the task (Snowflake refuses a schedule above 11520 minutes, 8 days), got '" ~ days ~ "'. To refresh less often, schedule your own task or run the refresh body by hand.") }}
  {%- endif -%}
  {%- set warehouse = warehouse or cfg.get('cortex_refresh_task_warehouse') or target.warehouse -%}
  {%- set resume = cfg.get('cortex_refresh_task_resume', false) if resume is none else resume -%}
  {%- set dbt_role = dbt_role or cfg.get('dbt_role') -%}
  {%- set names = icecore._icecore_refresh_names(schema) -%}
  {%- set task = names['task'] -%}
  {%- set body = icecore.render_refresh_cortex_models_sql(schema, min_points=min_points) -%}
  {%- set ddl = icecore._icecore_refresh_task_ddl(task, body, warehouse, days * 1440) -%}
  {%- set before = icecore._icecore_refresh_task_state(names) -%}
  {%- set status = icecore._icecore_guarded_ddl(ddl) -%}
  {%- do out.update({'task': task, 'status': status}) -%}
  {#- Models cannot be shared across roles: they are trained and scored in setup's role, through ICECORE_SCORE. The role that runs
      dbt needs only USAGE on that procedure, never on a model. Setup's own role (target.role) is not that role. -#}
  {%- do out['printed'].append('GRANT USAGE ON PROCEDURE ' ~ names['score'] ~ '(STRING, STRING, ARRAY, FLOAT, FLOAT) TO ROLE '
      ~ (icecore._icecore_identifier(dbt_role) if dbt_role else '<the role that runs dbt>')) -%}
  {%- do log('icecore: the role that runs dbt scores with Cortex through ICECORE_SCORE and needs only USAGE on it'
      ~ ('' if dbt_role else ' (set var icecore.dbt_role to name it in the grant)') ~ '; an admin must run: ' ~ out['printed'][0], info=True) -%}
  {%- if status != 'ok' -%}
    {%- if 'insufficient' in status | lower or 'not authorized' in status | lower or 'does not exist or not authorized' in status | lower -%}
      {%- do out['printed'].append('GRANT CREATE TASK ON SCHEMA ' ~ names['qualified'] ~ ' TO ROLE ' ~ target.role) -%}
      {%- do log('icecore: warning: could not set up ' ~ task ~ ' (' ~ status ~ '); an admin must run: ' ~ out['printed'][-1], info=True) -%}
    {%- else -%}
      {%- do log('icecore: warning: could not set up ' ~ task ~ ': ' ~ status, info=True) -%}
    {%- endif -%}
    {{ return(out) }}
  {%- endif -%}
  {#- CREATE OR REPLACE suspended a task an admin had resumed: resume it again. A new task stays suspended unless the var says so. -#}
  {%- set was_started = (before == 'started') -%}
  {%- if was_started or resume is sameas true or (resume | string | lower) == 'true' -%}
    {%- set resumed = icecore._icecore_guarded_ddl('ALTER TASK ' ~ task ~ ' RESUME') -%}
    {%- if resumed == 'ok' -%}
      {%- do out.update({'resumed': true}) -%}
      {%- if was_started -%}{%- do log('icecore: ' ~ task ~ ' was resumed before setup and is resumed again', info=True) -%}{%- endif -%}
    {%- else -%}
      {%- do out['printed'].append('GRANT EXECUTE TASK ON ACCOUNT TO ROLE ' ~ target.role) -%}
      {%- do log('icecore: warning: could not resume ' ~ task ~ ' (' ~ resumed ~ '); it stays suspended' ~ (' although it was resumed before setup' if was_started else '') ~ '. An admin must run: ' ~ out['printed'][-1], info=True) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- Every name that goes into the task text, validated: the database and the obs schema against [A-Za-z0-9_] (a compile error
    otherwise: no quote, no $, no control character), then quoted. `database` is the target's; the argument exists for tests.
    Quoted by icecore._sql(..., 'ident'). -#}
{% macro _icecore_refresh_names(schema=none, database=none) %}
  {%- set db = icecore._icecore_score_constant(target.database if database is none else database) -%}
  {%- set sch = icecore._icecore_score_constant(schema or var('icecore', {}).get('obs_schema', 'ICECORE_OBS')) -%}
  {{ return({
      'qualified': icecore._sql([db, sch], 'ident'), 'schema_name': sch,
      'metrics': icecore._sql([db, sch, 'METRICS'], 'ident'), 'emit': icecore._sql([db, sch, 'ICECORE_EMIT'], 'ident'),
      'score': icecore._sql([db, sch, 'ICECORE_SCORE'], 'ident'), 'task': icecore._sql([db, sch, 'ICECORE_REFRESH_CORTEX'], 'ident'),
  }) }}
{% endmacro %}


{#- The task's state before setup replaces it: 'started', 'suspended', 'none' (no such task) or 'unknown' (SHOW refused). -#}
{% macro _icecore_refresh_task_state(names) %}
  {%- set block -%}
declare
  s STRING DEFAULT 'none';
begin
  show tasks like 'ICECORE_REFRESH_CORTEX' in schema {{ names['qualified'] }};
  select coalesce(max(lower("state")), 'none') into :s from table(result_scan(last_query_id()));
  return s;
exception
  when other then return 'unknown';
end;
  {%- endset -%}
  {{ return(run_query('execute immediate ' ~ icecore._sql(block, 'string')).rows[0][0]) }}
{% endmacro %}


{% macro _icecore_refresh_task_ddl(task, body, warehouse, schedule_minutes) %}
  {%- if schedule_minutes is not integer or schedule_minutes < 1 or schedule_minutes > 11520 -%}
    {{ exceptions.raise_compiler_error('icecore: a task schedule must be 1 to 11520 minutes (Snowflake refuses more), got ' ~ schedule_minutes) }}
  {%- endif -%}
  {%- if '$$' in body -%}
    {{ exceptions.raise_compiler_error('icecore: the task body must not contain $$ (it would end the EXECUTE IMMEDIATE body early)') }}
  {%- endif -%}
  {#- The body is constant text (render_refresh_cortex_models_sql): validated names and integers only, no `$$` (checked above). -#}
  {%- set ddl -%}
CREATE OR REPLACE TASK {{ task }}{{ '\n  WAREHOUSE = ' ~ icecore._icecore_identifier(warehouse) if warehouse else '' }}
  SCHEDULE = {{ icecore._sql(schedule_minutes ~ ' MINUTE', 'string') }}
  COMMENT = 'icecore: retrains the Cortex anomaly models from the metric history'
AS
EXECUTE IMMEDIATE $$
{{ body }}
$$
  {%- endset -%}
  {{ return(ddl) }}
{% endmacro %}
