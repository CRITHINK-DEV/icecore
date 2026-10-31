{#-
  Spike S1, encoding E3 (the spec's fallback): rows in a hybrid table owned by the package.
  Creates ICECORE_SANDBOX.SPIKES.S1_H_<run_id>; the test reads it, then drops it.
  Inserts n committed rows in one statement, then one row inside a transaction that is rolled back.
-#}
{% macro s1_hybrid(run_id, n, attrs) %}
  {% if execute %}
    {% set t = target.database ~ '.' ~ target.schema ~ '.S1_H_' ~ (run_id | upper) %}
    {% set a = s1_sql_literal(_s1_attrs_json(run_id, attrs)) %}
    {% do run_query('CREATE HYBRID TABLE ' ~ t ~ ' (id INTEGER PRIMARY KEY, kind STRING, ts TIMESTAMP_LTZ, attrs VARIANT)') %}
    {% do run_query('INSERT INTO ' ~ t ~ ' SELECT SEQ4() + 1, \'committed\', CURRENT_TIMESTAMP(), PARSE_JSON(' ~ a ~ ') FROM TABLE(GENERATOR(ROWCOUNT => ' ~ (n | int) ~ '))') %}
    {% do run_query('BEGIN TRANSACTION') %}
    {% do run_query('INSERT INTO ' ~ t ~ ' SELECT 0, \'rolled_back\', CURRENT_TIMESTAMP(), PARSE_JSON(' ~ a ~ ')') %}
    {% do run_query('ROLLBACK') %}
  {% endif %}
{% endmacro %}
