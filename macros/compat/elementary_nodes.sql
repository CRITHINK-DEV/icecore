{#- UC8 compat views `dbt_models`, `dbt_tests` and `dbt_sources`, with the columns, order and types of Elementary's tables
    of those names (Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION: get_dbt_models_empty_table_query,
    get_dbt_tests_empty_table_query and get_dbt_sources_empty_table_query). They select from icecore's core views only
    (NODE_EXECUTIONS, TEST_RESULTS, LINEAGE_LATEST), never from base_events.

    icecore holds no manifest metadata (spike S4): what these views know is what the run records say. A node appears once
    it has run, so:
      dbt_models   one row per model that ran (its latest execution in NODE_EXECUTIONS): unique_id, name, materialization,
                   alias, database_name and schema_name (read from the node's relation "DB"."SCHEMA"."ALIAS"), package_name
                   (from the unique_id), generated_at (text of the latest record time, Snowflake's clock) and
                   depends_on_nodes (JSON list of the node's parents in LINEAGE_LATEST, the latest invocation's graph, null when it has none).
      dbt_tests    one row per test that ran (its latest execution joined to TEST_RESULTS): unique_id, name, short_name
                   (the test's name), test_column_name, severity, type (generic or singular), package_name, generated_at.
                   Tests are not lineage edges, so depends_on_nodes is NULL; parent_model_unique_id is the tested node.
      dbt_sources  one row per source that is a parent in LINEAGE_LATEST: unique_id, source_name, name, package_name (from
                   the unique_id source.<package>.<source>.<table>) and generated_at (the latest lineage record time).
    Every other column is a typed NULL (description, tags, meta, owner, paths, checksum and hashes, database and schema of
    a test or source, freshness settings, ...). Text timestamps are `YYYY-MM-DD HH24:MI:SS`, which Snowflake can cast. -#}


{#- The group of views appended to the compat list: [{name, sql}]. -#}
{% macro _icecore_elementary_node_views(obs) %}
  {{ return([
    {'name': 'DBT_MODELS', 'sql': icecore.dbt_models_view_sql(obs)},
    {'name': 'DBT_TESTS', 'sql': icecore.dbt_tests_view_sql(obs)},
    {'name': 'DBT_SOURCES', 'sql': icecore.dbt_sources_view_sql(obs)},
  ]) }}
{% endmacro %}


{% macro dbt_models_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {%- set source -%}
(
    select n.unique_id, n.name, n.materialization, n.relation, n.recorded_at, p.parents
    from {{ obs }}."NODE_EXECUTIONS" as n
    left join (
        select child_unique_id, to_json(array_agg(parent_unique_id) within group (order by parent_unique_id)) as parents
        from {{ obs }}."LINEAGE_LATEST"
        group by child_unique_id
    ) as p on p.child_unique_id = n.unique_id
    where n.type = 'model'
    qualify row_number() over (partition by n.unique_id order by n.recorded_at desc nulls last, n.invocation_id desc) = 1
)
  {%- endset -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_dbt_models_columns(obs), source)) }}
{% endmacro %}


{% macro dbt_tests_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {%- set source -%}
(
    select n.unique_id, n.name, n.recorded_at, r.test_name, r.test_type, r."COLUMN" as test_column, r.severity, r.tested_unique_id
    from {{ obs }}."NODE_EXECUTIONS" as n
    left join {{ obs }}."TEST_RESULTS" as r on r.invocation_id = n.invocation_id and r.test_unique_id = n.unique_id
    where n.type = 'test'
    qualify row_number() over (partition by n.unique_id order by n.recorded_at desc nulls last, n.invocation_id desc) = 1
)
  {%- endset -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_dbt_tests_columns(obs), source)) }}
{% endmacro %}


{% macro dbt_sources_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {%- set source -%}
(
    select parent_unique_id as unique_id, max(recorded_at) as recorded_at
    from {{ obs }}."LINEAGE_LATEST"
    where parent_unique_id like 'source.%'
    group by parent_unique_id
)
  {%- endset -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_dbt_sources_columns(obs), source)) }}
{% endmacro %}


{#- [{name, type, expr}] in Elementary's order; none = typed NULL. -#}
{% macro _icecore_dbt_models_columns(obs) %}
  {%- set text = icecore._icecore_elementary_text -%}
  {{ return([
    {'name': 'unique_id', 'type': 'string', 'expr': "t.unique_id"},
    {'name': 'alias', 'type': 'string', 'expr': "replace(split_part(t.relation, '.', 3), chr(34), '')"},
    {'name': 'checksum', 'type': 'string', 'expr': none},
    {'name': 'materialization', 'type': 'string', 'expr': "t.materialization"},
    {'name': 'tags', 'type': 'long_string', 'expr': none},
    {'name': 'meta', 'type': 'long_string', 'expr': none},
    {'name': 'owner', 'type': 'string', 'expr': none},
    {'name': 'database_name', 'type': 'string', 'expr': "replace(split_part(t.relation, '.', 1), chr(34), '')"},
    {'name': 'schema_name', 'type': 'string', 'expr': "replace(split_part(t.relation, '.', 2), chr(34), '')"},
    {'name': 'depends_on_macros', 'type': 'long_string', 'expr': none},
    {'name': 'depends_on_nodes', 'type': 'long_string', 'expr': "t.parents"},
    {'name': 'description', 'type': 'long_string', 'expr': none},
    {'name': 'name', 'type': 'string', 'expr': "t.name"},
    {'name': 'package_name', 'type': 'string', 'expr': "split_part(t.unique_id, '.', 2)"},
    {'name': 'original_path', 'type': 'long_string', 'expr': none},
    {'name': 'path', 'type': 'string', 'expr': none},
    {'name': 'patch_path', 'type': 'string', 'expr': none},
    {'name': 'generated_at', 'type': 'string', 'expr': text('t.recorded_at')},
    {'name': 'metadata_hash', 'type': 'string', 'expr': none},
    {'name': 'unique_key', 'type': 'string', 'expr': none},
    {'name': 'incremental_strategy', 'type': 'string', 'expr': none},
    {'name': 'group_name', 'type': 'string', 'expr': none},
    {'name': 'access', 'type': 'string', 'expr': none},
  ]) }}
{% endmacro %}


{#- [{name, type, expr}] in Elementary's order; none = typed NULL. -#}
{% macro _icecore_dbt_tests_columns(obs) %}
  {%- set text = icecore._icecore_elementary_text -%}
  {{ return([
    {'name': 'unique_id', 'type': 'string', 'expr': "t.unique_id"},
    {'name': 'database_name', 'type': 'string', 'expr': none},
    {'name': 'schema_name', 'type': 'string', 'expr': none},
    {'name': 'name', 'type': 'string', 'expr': "t.name"},
    {'name': 'short_name', 'type': 'string', 'expr': "t.test_name"},
    {'name': 'alias', 'type': 'string', 'expr': none},
    {'name': 'test_column_name', 'type': 'string', 'expr': "t.test_column"},
    {'name': 'severity', 'type': 'string', 'expr': "t.severity"},
    {'name': 'warn_if', 'type': 'string', 'expr': none},
    {'name': 'error_if', 'type': 'string', 'expr': none},
    {'name': 'test_params', 'type': 'long_string', 'expr': none},
    {'name': 'test_namespace', 'type': 'string', 'expr': none},
    {'name': 'test_original_name', 'type': 'string', 'expr': none},
    {'name': 'tags', 'type': 'long_string', 'expr': none},
    {'name': 'model_tags', 'type': 'long_string', 'expr': none},
    {'name': 'model_owners', 'type': 'long_string', 'expr': none},
    {'name': 'meta', 'type': 'long_string', 'expr': none},
    {'name': 'depends_on_macros', 'type': 'long_string', 'expr': none},
    {'name': 'depends_on_nodes', 'type': 'long_string', 'expr': none},
    {'name': 'parent_model_unique_id', 'type': 'string', 'expr': 't.tested_unique_id'},
    {'name': 'description', 'type': 'long_string', 'expr': none},
    {'name': 'package_name', 'type': 'string', 'expr': "split_part(t.unique_id, '.', 2)"},
    {'name': 'type', 'type': 'string', 'expr': "t.test_type"},
    {'name': 'original_path', 'type': 'long_string', 'expr': none},
    {'name': 'path', 'type': 'string', 'expr': none},
    {'name': 'generated_at', 'type': 'string', 'expr': text('t.recorded_at')},
    {'name': 'metadata_hash', 'type': 'string', 'expr': none},
    {'name': 'quality_dimension', 'type': 'string', 'expr': none},
    {'name': 'group_name', 'type': 'string', 'expr': none},
  ]) }}
{% endmacro %}


{#- [{name, type, expr}] in Elementary's order; none = typed NULL. -#}
{% macro _icecore_dbt_sources_columns(obs) %}
  {%- set text = icecore._icecore_elementary_text -%}
  {{ return([
    {'name': 'unique_id', 'type': 'string', 'expr': "t.unique_id"},
    {'name': 'database_name', 'type': 'string', 'expr': none},
    {'name': 'schema_name', 'type': 'string', 'expr': none},
    {'name': 'source_name', 'type': 'string', 'expr': "split_part(t.unique_id, '.', 3)"},
    {'name': 'name', 'type': 'string', 'expr': "split_part(t.unique_id, '.', 4)"},
    {'name': 'identifier', 'type': 'string', 'expr': none},
    {'name': 'loaded_at_field', 'type': 'string', 'expr': none},
    {'name': 'freshness_warn_after', 'type': 'string', 'expr': none},
    {'name': 'freshness_error_after', 'type': 'string', 'expr': none},
    {'name': 'freshness_filter', 'type': 'long_string', 'expr': none},
    {'name': 'freshness_description', 'type': 'long_string', 'expr': none},
    {'name': 'relation_name', 'type': 'string', 'expr': none},
    {'name': 'tags', 'type': 'long_string', 'expr': none},
    {'name': 'meta', 'type': 'long_string', 'expr': none},
    {'name': 'owner', 'type': 'string', 'expr': none},
    {'name': 'package_name', 'type': 'string', 'expr': "split_part(t.unique_id, '.', 2)"},
    {'name': 'original_path', 'type': 'long_string', 'expr': none},
    {'name': 'path', 'type': 'string', 'expr': none},
    {'name': 'source_description', 'type': 'long_string', 'expr': none},
    {'name': 'description', 'type': 'long_string', 'expr': none},
    {'name': 'generated_at', 'type': 'string', 'expr': text('t.recorded_at')},
    {'name': 'metadata_hash', 'type': 'string', 'expr': none},
  ]) }}
{% endmacro %}

