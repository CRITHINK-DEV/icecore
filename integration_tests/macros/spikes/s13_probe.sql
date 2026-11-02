{% macro s13_probe() %}
  {% if execute %}
    {% do log('S13 dbt_version=' ~ dbt_version, info=true) %}
    {% do log('S13 which=' ~ flags.WHICH, info=true) %}
    {% do log('S13 args=' ~ tojson(invocation_args_dict), info=true) %}
    {% do log('S13 target=' ~ tojson({'name': target.name, 'type': target.type, 'account': target.get('account'), 'user': target.get('user'), 'role': target.get('role'), 'database': target.database, 'schema': target.schema, 'threads': target.threads, 'warehouse': target.get('warehouse'), 'keys': target.keys() | list}), info=true) %}
    {% do log('S13 project_root=' ~ project_name, info=true) %}
    {% do log('S13 flags_project_dir=' ~ (flags.PROJECT_DIR | string) ~ ' profiles_dir=' ~ (flags.PROFILES_DIR | string) ~ ' log_path=' ~ (flags.LOG_PATH | string) ~ ' target_path=' ~ (flags.TARGET_PATH | string), info=true) %}
    {% for n in ['DBT_CLOUD_RUN_ID','SNOWFLAKE_ACCOUNT','SNOWFLAKE_HOST','SNOWFLAKE_USER','SNOWFLAKE_ROLE','HOME','PWD','USER','SNOWFLAKE_DBT_PROJECT','DBT_PROJECT_NAME','DBT_PROFILES_DIR','DBT_PROJECT_DIR','SNOWFLAKE_QUERY_ID','SNOWFLAKE_WAREHOUSE','SNOWFLAKE_DATABASE','SNOWFLAKE_SCHEMA','SNOWFLAKE_SESSION_TOKEN','SNOWFLAKE_CONNECTION_NAME','HOSTNAME','DBT_ENV_CUSTOM_ENV_X','SNOWPARK','SNOWFLAKE_ENVIRONMENT','SNOWFLAKE_EXECUTABLE_TYPE','SNOWFLAKE_EXECUTABLE_NAME','SNOWFLAKE_RUNTIME'] %}
      {% do log('S13 env ' ~ n ~ '=' ~ (env_var(n, '<unset>') if n not in ['SNOWFLAKE_SESSION_TOKEN'] else (('<set>' if env_var(n,'') else '<unset>'))), info=true) %}
    {% endfor %}
    {% set r = run_query("select current_query_id_placeholder") if false else none %}
    {% set rows = run_query("select current_session() s, current_role() r, current_client() c, current_statement() st") %}
    {% do log('S13 session=' ~ rows.rows[0] | string, info=true) %}
    {% set qt = run_query("show parameters like 'QUERY_TAG' in session") %}
    {% do log('S13 query_tag=' ~ qt.rows | string, info=true) %}
    {% set lv = run_query("show parameters like '%LEVEL%' in session") %}
    {% do log('S13 levels=' ~ lv.rows | string, info=true) %}
  {% endif %}
{% endmacro %}
