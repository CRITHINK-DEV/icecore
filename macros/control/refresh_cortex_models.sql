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
    stop the others) that reads only the metrics view. It returns 'ok <n> models' or 'icecore-error ...'. -#}


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


{#- The task body: a Scripting block. `source` is the relation read as the metrics view (default the obs schema's METRICS).

    No SQL text is built from a record value. `relation` comes from event records the dbt role can write, so it is only
    ever (1) checked against the strict quoted-identifier shape "DB"."SCHEMA"."NAME" (anything else is skipped and counted),
    (2) hashed (MD5) to name the model and to filter the training rows, and (3) passed as a bound value inside
    OBJECT_CONSTRUCT to the mapping event. The statements that are built from text contain only this macro's own constants,
    hex digests and integers; the series key is hashed in SQL (MD5), never pasted. The one string literal in the body that
    carries a value, the digest, is a hex string. -#}
{% macro render_refresh_cortex_models_sql(schema=none, source=none, lookback_days=30, delay_minutes=none, min_points=14) %}
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
  {%- set source = source or icecore._icecore_procedure('METRICS', schema) -%}
  {%- set emit = icecore._icecore_procedure('ICECORE_EMIT', schema) -%}
  {%- set prefix = icecore.obs_schema(schema) ~ '."' -%}
  {%- set window = 'value is not null and observed_at > dateadd(day, -' ~ lookback_days ~ ', sysdate()) and observed_at <= dateadd(minute, -' ~ delay ~ ', sysdate())' -%}
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
BEGIN
  FOR r IN rels DO
    -- a relation is a record value: only the strict shape "DB"."SCHEMA"."NAME" (no quote inside a part) goes on
    IF (r.relation IS NULL OR NOT REGEXP_LIKE(r.relation, '"[^"]+"\\."[^"]+"\\."[^"]+"')) THEN
      skipped := skipped + 1;
    ELSE
      BEGIN
        digest := MD5(r.relation);
        model_id := 'ICECORE_M_' || UPPER(LEFT(digest, 16));
        train := 'select md5(nvl(metric, chr(126)) || chr(124) || nvl("COLUMN", chr(126)) || chr(124) || nvl(dimension, chr(126))) as series, '
                 || 'observed_at as ts, value as y from (select metric, "COLUMN", dimension, observed_at, value, '
                 || 'count(*) over (partition by metric, "COLUMN", dimension) as n from {{ source }} '
                 || 'where md5(relation) = ' || qq || digest || qq || ' and {{ window }}) where n >= {{ min_points }}';
        EXECUTE IMMEDIATE 'CREATE OR REPLACE SNOWFLAKE.ML.ANOMALY_DETECTION {{ prefix }}' || model_id || '"'
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
    RETURN 'icecore-error ' || ARRAY_SIZE(created) || ' models, ' || skipped || ' skipped, ' || ARRAY_SIZE(failed) || ' failed: ' || ARRAY_TO_STRING(failed, '; ');
  END IF;
  RETURN 'ok ' || ARRAY_SIZE(created) || ' models, ' || skipped || ' skipped';
EXCEPTION
  WHEN OTHER THEN RETURN 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
END;
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Creates the task ICECORE_REFRESH_CORTEX in the obs schema (setup's create_tasks step), only when Cortex is available
    (`capabilities`, a detect_capabilities result; default detects, without recording). Returns {task, status, resumed,
    printed}: status ok, skipped (Cortex off) or the guarded error. Created SUSPENDED: var icecore.cortex_refresh_task_resume:
    true resumes it. Schedule: var icecore.cortex_refresh_days (default 7) as minutes. Warehouse: var
    cortex_refresh_task_warehouse, else the target's. `printed` holds what an admin must run: CREATE TASK, EXECUTE TASK
    to resume, and the USAGE grant on ICECORE_SCORE for the role that runs dbt (models are never granted). -#}
{% macro create_refresh_cortex_task(schema=none, capabilities=none, source=none, warehouse=none, resume=none, days=none, min_points=14) %}
  {%- set out = {'task': none, 'status': 'skipped', 'resumed': false, 'printed': []} -%}
  {%- if not execute -%}{{ return(out) }}{%- endif -%}
  {%- set cfg = var('icecore', {}) -%}
  {%- set capabilities = icecore.detect_capabilities(record=false) if capabilities is none else capabilities -%}
  {%- if capabilities.get('cortex') is not sameas true -%}
    {%- do log('icecore: Cortex refresh task not created: Cortex is not available (' ~ capabilities.get('cortex_reason') ~ ')', info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set days = cfg.get('cortex_refresh_days', 7) if days is none else days -%}
  {%- if days is not integer or days < 1 -%}
    {{ exceptions.raise_compiler_error("icecore: cortex_refresh_days must be a whole number of days, 1 or more, to schedule the task, got '" ~ days ~ "'") }}
  {%- endif -%}
  {%- set warehouse = warehouse or cfg.get('cortex_refresh_task_warehouse') or target.warehouse -%}
  {%- set resume = cfg.get('cortex_refresh_task_resume', false) if resume is none else resume -%}
  {%- set task = icecore._icecore_procedure('ICECORE_REFRESH_CORTEX', schema) -%}
  {%- set body = icecore.render_refresh_cortex_models_sql(schema, source, min_points=min_points) -%}
  {%- set ddl = icecore._icecore_refresh_task_ddl(task, body, warehouse, days * 1440) -%}
  {%- set status = icecore._icecore_guarded_ddl(ddl) -%}
  {%- do out.update({'task': task, 'status': status}) -%}
  {#- Models cannot be shared across roles: they are trained and scored in setup's role, through ICECORE_SCORE. The dbt role needs
      only USAGE on that procedure, never on a model. -#}
  {%- do out['printed'].append('GRANT USAGE ON PROCEDURE ' ~ icecore._icecore_procedure('ICECORE_SCORE', schema)
      ~ '(STRING, STRING, ARRAY, FLOAT, FLOAT) TO ROLE ' ~ target.role) -%}
  {%- do log('icecore: the role that runs dbt scores with Cortex through ICECORE_SCORE and needs only USAGE on it; an admin must run: '
      ~ out['printed'][0], info=True) -%}
  {%- if status != 'ok' -%}
    {%- do out['printed'].append('GRANT CREATE TASK ON SCHEMA ' ~ icecore.obs_schema(schema) ~ ' TO ROLE ' ~ target.role) -%}
    {%- do log('icecore: warning: could not set up ' ~ task ~ ' (' ~ status ~ '); an admin must run: ' ~ out['printed'][-1], info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- if resume is sameas true or (resume | string | lower) == 'true' -%}
    {%- set resumed = icecore._icecore_guarded_ddl('ALTER TASK ' ~ task ~ ' RESUME') -%}
    {%- if resumed == 'ok' -%}
      {%- do out.update({'resumed': true}) -%}
    {%- else -%}
      {%- do out['printed'].append('GRANT EXECUTE TASK ON ACCOUNT TO ROLE ' ~ target.role) -%}
      {%- do log('icecore: warning: could not resume ' ~ task ~ ' (' ~ resumed ~ '); it stays suspended. An admin must run: ' ~ out['printed'][-1], info=True) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{% macro _icecore_refresh_task_ddl(task, body, warehouse, schedule_minutes) %}
  {{ return('CREATE OR REPLACE TASK ' ~ task
      ~ ('\n  WAREHOUSE = ' ~ icecore._icecore_identifier(warehouse) if warehouse else '')
      ~ '\n  SCHEDULE = ' ~ icecore._icecore_sql_literal(schedule_minutes ~ ' MINUTE')
      ~ "\n  COMMENT = 'icecore: retrains the Cortex anomaly models from the metric history'"
      ~ '\nAS\nEXECUTE IMMEDIATE $$\n' ~ body ~ '\n$$') }}
{% endmacro %}
