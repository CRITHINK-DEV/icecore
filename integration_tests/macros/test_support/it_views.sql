{#- Test wrappers for the icecore views (macros/views/). Results are logged as `icecore_it_result:<json>`. -#}

{% macro it_create_views(schema=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.create_views(schema)), info=True) %}
{% endmacro %}

{#- Renders without running: the list of views, each {name, sql}. -#}
{% macro it_views_render(schema=none, event_table='DB.TELEMETRY.EVENTS') %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_views(schema, event_table)), info=True) %}
{% endmacro %}

{% macro it_view_ddl(view, sql, schema=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_view_ddl(view, sql, schema)), info=True) %}
{% endmacro %}
