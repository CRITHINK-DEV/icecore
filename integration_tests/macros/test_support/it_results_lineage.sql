{#- Test wrapper for tests/test_results_lineage_views.py: sends items to ICECORE_EMIT_RUN as end_run does. -#}
{% macro it_emit_run_items(items) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.emit_run(items)), info=True) %}
{% endmacro %}

{% macro it_detect_capabilities() %}
  {% do log('icecore_it_result:' ~ tojson(icecore.detect_capabilities()), info=True) %}
{% endmacro %}
