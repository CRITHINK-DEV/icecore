{#- UC8 compat view `elementary_test_results`: one row per test per invocation, with the columns, order and types
    of Elementary's table of that name (Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION,
    macro empty_elementary_test_results). It selects from the core view TEST_RESULTS of the obs schema and nothing
    else (no base_events), so it has exactly the core view's rows: row counts match.

    What it fills, from TEST_RESULTS:
      id, test_execution_id      invocation_id || '.' || test_unique_id (one row per test per invocation)
      test_unique_id, invocation_id
      detected_at, created_at    executed_at, as TIMESTAMP_NTZ
      column_name                "COLUMN"
      test_type                  anomaly_detection when the test was scored (engine is set), else dbt_test
      test_sub_type              the core test_type (generic or singular) for dbt_test, NULL for anomaly_detection
      test_results_description   message (redacted by default, var icecore.result_message)
      test_name, test_short_name test_name
      severity, status           lower case, as Elementary writes dbt's values (warn, error; pass, fail, warn, error)
      failures, failed_row_count failures
      other                      for scored tests a JSON object {engine, expected, lower, upper, score}, else NULL
    Typed NULLs (icecore has no source for them): data_issue_id, model_unique_id, database_name, schema_name,
    table_name, owners, tags, test_results_query, test_params, test_alias, result_rows.
    Elementary stores timestamps as TIMESTAMP_NTZ here (detected_at, created_at) and strings as VARCHAR. -#}


{#- The group of views appended to the compat list: [{name, sql}]. -#}
{% macro _icecore_elementary_test_views(obs) %}
  {{ return([{'name': 'ELEMENTARY_TEST_RESULTS', 'sql': icecore.elementary_test_results_view_sql(obs)}]) }}
{% endmacro %}


{#- The SELECT of elementary_test_results. obs is the quoted obs schema ("DB"."SCHEMA"), default the current one. -#}
{% macro elementary_test_results_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_elementary_test_results_columns(), obs ~ '."TEST_RESULTS"')) }}
{% endmacro %}


{#- [{name, type, expr}] in Elementary's order. expr is over the alias t (TEST_RESULTS), none = typed NULL. -#}
{% macro _icecore_elementary_test_results_columns() %}
  {%- set scored = 't.engine is not null' -%}
  {{ return([
    {'name': 'id', 'type': 'long_string', 'expr': "t.invocation_id || '.' || t.test_unique_id"},
    {'name': 'data_issue_id', 'type': 'string', 'expr': none},
    {'name': 'test_execution_id', 'type': 'long_string', 'expr': "t.invocation_id || '.' || t.test_unique_id"},
    {'name': 'test_unique_id', 'type': 'long_string', 'expr': 't.test_unique_id'},
    {'name': 'model_unique_id', 'type': 'long_string', 'expr': none},
    {'name': 'invocation_id', 'type': 'string', 'expr': 't.invocation_id'},
    {'name': 'detected_at', 'type': 'timestamp', 'expr': 't.executed_at'},
    {'name': 'created_at', 'type': 'timestamp', 'expr': 't.executed_at'},
    {'name': 'database_name', 'type': 'string', 'expr': none},
    {'name': 'schema_name', 'type': 'string', 'expr': none},
    {'name': 'table_name', 'type': 'string', 'expr': none},
    {'name': 'column_name', 'type': 'string', 'expr': 't."COLUMN"'},
    {'name': 'test_type', 'type': 'string', 'expr': "iff(" ~ scored ~ ", 'anomaly_detection', 'dbt_test')"},
    {'name': 'test_sub_type', 'type': 'string', 'expr': "iff(" ~ scored ~ ", null, t.test_type)"},
    {'name': 'test_results_description', 'type': 'long_string', 'expr': 't.message'},
    {'name': 'owners', 'type': 'string', 'expr': none},
    {'name': 'tags', 'type': 'string', 'expr': none},
    {'name': 'test_results_query', 'type': 'long_string', 'expr': none},
    {'name': 'other', 'type': 'string', 'expr': "iff(" ~ scored ~ ", to_json(object_construct_keep_null('engine', t.engine, 'expected', t.expected, 'lower', t.lower, 'upper', t.upper, 'score', t.score)), null)"},
    {'name': 'test_name', 'type': 'long_string', 'expr': 't.test_name'},
    {'name': 'test_params', 'type': 'long_string', 'expr': none},
    {'name': 'severity', 'type': 'string', 'expr': 'lower(t.severity)'},
    {'name': 'status', 'type': 'string', 'expr': 'lower(t.status)'},
    {'name': 'failures', 'type': 'bigint', 'expr': 't.failures'},
    {'name': 'test_short_name', 'type': 'string', 'expr': 't.test_name'},
    {'name': 'test_alias', 'type': 'string', 'expr': none},
    {'name': 'result_rows', 'type': 'long_string', 'expr': none},
    {'name': 'failed_row_count', 'type': 'bigint', 'expr': 't.failures'},
  ]) }}
{% endmacro %}
