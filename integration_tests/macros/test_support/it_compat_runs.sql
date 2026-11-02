{#- Test wrappers for the UC8 run compat views (macros/compat/elementary_runs.sql). Logged as `icecore_it_result:<json>`. -#}

{% macro it_compat_runs() %}
  {% do log('icecore_it_result:' ~ tojson({
      'invocations_sql': icecore.dbt_invocations_view_sql('"DB"."OBS"'),
      'invocations': icecore._icecore_dbt_invocations_columns(),
      'run_results_sql': icecore.dbt_run_results_view_sql('"DB"."OBS"'),
      'run_results': icecore._icecore_dbt_run_results_columns(),
      'version': icecore._icecore_elementary_version(),
  }), info=True) %}
{% endmacro %}
