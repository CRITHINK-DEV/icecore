{#- Test wrapper for tests/test_render_alert.py: create_alerts, result logged as `icecore_it_result:<json>`. -#}
{% macro it_create_alerts(schema=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.create_alerts(schema)), info=True) %}
{% endmacro %}
