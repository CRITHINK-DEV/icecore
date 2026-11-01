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

{#- Name of a table's model in the obs schema: ICECORE_CX_<relation, upper, non A-Z0-9_ as _, cut to 200>_<first 8 hex of
    md5(relation)>, fully qualified and quoted. `relation` is the quoted "DB"."SCHEMA"."NAME" the metrics view carries,
    or a dbt relation. The task computes the same name in SQL. -#}
{% macro cortex_model_name(relation, schema=none) %}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set stem = modules.re.sub('[^A-Z0-9_]', '_', name | upper)[:200] -%}
  {{ return(icecore.obs_schema(schema) ~ '."ICECORE_CX_' ~ stem ~ '_' ~ (local_md5(name)[:8] | upper) ~ '"') }}
{% endmacro %}


{#- The series key a model is trained with: the JSON array [metric, column, dimension] (null as null), compact:
    TO_JSON of the array [metric, column, dimension] with JSON nulls in the task (a SQL NULL would print as `undefined`). Pass it as `series` to score. -#}
{% macro cortex_series_key(metric, column=none, dimension=none) %}
  {%- set parts = [] -%}
  {%- for part in [metric, column, dimension] -%}
    {%- do parts.append('null' if part is none else tojson(part | string)) -%}
  {%- endfor -%}
  {{ return('[' ~ parts | join(',') ~ ']') }}
{% endmacro %}


{#- The task body: a Scripting block. `source` is the relation read as the metrics view (default the obs schema's METRICS). -#}
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
  {%- set prefix = icecore.obs_schema(schema) ~ '."ICECORE_CX_' -%}
  {%- set window = 'value is not null and observed_at > dateadd(day, -' ~ lookback_days ~ ', sysdate()) and observed_at <= dateadd(minute, -' ~ delay ~ ', sysdate())' -%}
  {%- set sql -%}
DECLARE
  q STRING DEFAULT CHR(39);
  created ARRAY DEFAULT ARRAY_CONSTRUCT();
  failed ARRAY DEFAULT ARRAY_CONSTRUCT();
  rels RESULTSET DEFAULT (
    SELECT relation FROM (
      SELECT relation, metric, "COLUMN", dimension, COUNT(*) AS n FROM {{ source }}
      WHERE {{ window }}
      GROUP BY relation, metric, "COLUMN", dimension)
    WHERE n >= {{ min_points }} GROUP BY relation);
  model STRING;
  train STRING;
BEGIN
  FOR r IN rels DO
    BEGIN
      model := {{ icecore._icecore_sql_literal(prefix) }} || LEFT(REGEXP_REPLACE(UPPER(r.relation), '[^A-Z0-9_]', '_'), 200)
               || '_' || UPPER(LEFT(MD5(r.relation), 8)) || '"';
      train := 'select to_json(array_construct(metric, ifnull(to_variant("COLUMN"), parse_json(''null'')), ifnull(to_variant(dimension), parse_json(''null'')))) as series, observed_at as ts, value as y from ('
               || 'select metric, "COLUMN", dimension, observed_at, value, count(*) over (partition by metric, "COLUMN", dimension) as n from {{ source }}'
               || ' where relation = ' || q || REPLACE(r.relation, q, q || q) || q || ' and {{ window }}) where n >= {{ min_points }}';
      EXECUTE IMMEDIATE 'CREATE OR REPLACE SNOWFLAKE.ML.ANOMALY_DETECTION ' || model
        || '(INPUT_DATA => SYSTEM$QUERY_REFERENCE(' || q || REPLACE(train, q, q || q) || q || '), SERIES_COLNAME => ' || q || 'SERIES' || q
        || ', TIMESTAMP_COLNAME => ' || q || 'TS' || q || ', TARGET_COLNAME => ' || q || 'Y' || q || ', LABEL_COLNAME => ' || q || q || ')';
      created := ARRAY_APPEND(created, model);
    EXCEPTION
      WHEN OTHER THEN failed := ARRAY_APPEND(failed, r.relation || ': ' || SQLCODE || ': ' || SQLERRM);
    END;
  END FOR;
  IF (ARRAY_SIZE(failed) > 0) THEN
    RETURN 'icecore-error ' || ARRAY_SIZE(created) || ' models, ' || ARRAY_SIZE(failed) || ' failed: ' || ARRAY_TO_STRING(failed, '; ');
  END IF;
  RETURN 'ok ' || ARRAY_SIZE(created) || ' models';
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
    to resume, and the USAGE grant on the model instances for the role that runs dbt (it scores with the models). -#}
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
  {%- do out['printed'].append('GRANT USAGE ON ALL SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA ' ~ icecore.obs_schema(schema) ~ ' TO ROLE ' ~ target.role) -%}
  {%- do out['printed'].append('GRANT USAGE ON FUTURE SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA ' ~ icecore.obs_schema(schema) ~ ' TO ROLE ' ~ target.role) -%}
  {%- do log('icecore: the role that runs dbt needs USAGE on the Cortex models (it scores with them); an admin must run: '
      ~ (out['printed'] | join('; ')), info=True) -%}
  {%- if status != 'ok' -%}
    {%- do out['printed'].insert(0, 'GRANT CREATE TASK ON SCHEMA ' ~ icecore.obs_schema(schema) ~ ' TO ROLE ' ~ target.role) -%}
    {%- do log('icecore: warning: could not create ' ~ task ~ ' (' ~ status ~ '); an admin must run: ' ~ out['printed'][0], info=True) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- if resume is sameas true or (resume | string | lower) == 'true' -%}
    {%- set resumed = icecore._icecore_guarded_ddl('ALTER TASK ' ~ task ~ ' RESUME') -%}
    {%- if resumed == 'ok' -%}
      {%- do out.update({'resumed': true}) -%}
    {%- else -%}
      {%- do out['printed'].insert(0, 'GRANT EXECUTE TASK ON ACCOUNT TO ROLE ' ~ target.role) -%}
      {%- do log('icecore: warning: could not resume ' ~ task ~ ' (' ~ resumed ~ '); it stays suspended. An admin must run: ' ~ out['printed'][0], info=True) -%}
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
