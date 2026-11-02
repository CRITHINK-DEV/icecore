{#- Test wrappers for the UC8 node compat views (macros/compat/elementary_nodes.sql). Logged as `icecore_it_result:<json>`. -#}

{% macro it_compat_nodes() %}
  {% do log('icecore_it_result:' ~ tojson({
      'models_sql': icecore.dbt_models_view_sql('"DB"."OBS"'),
      'models': icecore._icecore_dbt_models_columns('"DB"."OBS"'),
      'tests_sql': icecore.dbt_tests_view_sql('"DB"."OBS"'),
      'tests': icecore._icecore_dbt_tests_columns('"DB"."OBS"'),
      'sources_sql': icecore.dbt_sources_view_sql('"DB"."OBS"'),
      'sources': icecore._icecore_dbt_sources_columns('"DB"."OBS"'),
  }), info=True) %}
{% endmacro %}
