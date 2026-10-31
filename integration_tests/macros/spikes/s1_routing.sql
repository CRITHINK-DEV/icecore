{#-
  Spike S1, routing: which event table receives a record depends on the database context of the
  executing statement. One span event and one log per variant, all tagged with run_id:
    anon_session_db            USE DATABASE/SCHEMA in the session, then anonymous blocks (E1)
    anon_use_db_inside         anonymous block that runs USE DATABASE itself before emitting
    anon_calls_qualified_proc  anonymous block that CALLs a fully qualified temporary procedure
    anon_python_min_packages   anonymous Python procedure without snowflake-telemetry-python (E2)
    temp_proc                  temporary procedure called directly (baseline)
  The no-database control runs from the test through a connection opened without a database.
  Every variant first sets LOG_LEVEL INFO and TRACE_LEVEL ALWAYS for the session, so that routing is
  measured apart from levels (an anonymous statement has no object or database level to inherit).
-#}
{% macro s1_routing(run_id, variant) %}
  {% if execute %}
    {% set db = target.database %}
    {% set attrs = {'icecore.routing': variant} %}
    {% do run_query("ALTER SESSION SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'") %}
    {% if variant == 'anon_session_db' %}
      {% do run_query('USE DATABASE ' ~ db) %}
      {% do run_query('USE SCHEMA ' ~ db ~ '.' ~ target.schema) %}
      {% do s1_sql_event(run_id, 1, attrs, 'anonymous') %}
      {% do s1_sql_log(run_id, 1, 'routing ' ~ variant, 'anonymous') %}
    {% elif variant == 'anon_use_db_inside' %}
      {% set sql %}
EXECUTE IMMEDIATE $$
DECLARE
  a OBJECT;
  m STRING DEFAULT {{ s1_sql_literal('routing ' ~ variant ~ ' ' ~ run_id ~ ' 1') }};
BEGIN
  USE DATABASE {{ db }};
  a := PARSE_JSON({{ s1_sql_literal(_s1_attrs_json(run_id, attrs)) }});
  SYSTEM$ADD_EVENT('icecore.probe', :a);
  SYSTEM$LOG_INFO(:m);
  RETURN 'ok';
END;
$$
      {% endset %}
      {% do run_query(sql) %}
    {% elif variant == 'anon_calls_qualified_proc' %}
      {% set ev = _s1_event_plan(run_id, 1, attrs, 'temp_procedure') %}
      {% set lg = _s1_log_plan(run_id, 1, 'routing ' ~ variant, 'temp_procedure') %}
      {% for s in ev['setup'] + lg['setup'] %}{% do run_query(s) %}{% endfor %}
      {% do run_query('EXECUTE IMMEDIATE $$\nBEGIN\n  ' ~ ev['call'] ~ ';\n  ' ~ lg['call'] ~ ";\n  RETURN 'ok';\nEND;\n$$") %}
      {% for s in ev['teardown'] + lg['teardown'] %}{% do run_query(s) %}{% endfor %}
    {% elif variant == 'anon_python_min_packages' %}
      {% do _s1_run(_s1_python_plan(run_id, 1, 1, attrs, 'routing ' ~ variant, 'anonymous', 0, 0,
                                    ['snowflake-snowpark-python', 'opentelemetry-api'])) %}
    {% elif variant == 'temp_proc' %}
      {% do s1_sql_event(run_id, 1, attrs, 'temp_procedure') %}
      {% do s1_sql_log(run_id, 1, 'routing ' ~ variant, 'temp_procedure') %}
    {% else %}
      {{ exceptions.raise_compiler_error("s1_routing: unknown variant '" ~ variant ~ "'") }}
    {% endif %}
  {% endif %}
{% endmacro %}
