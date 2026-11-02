{#- Offline: icecore._icecore_validate_window_tag(tag); logs true or raises. -#}
{% macro it_validate_window_tag(tag) %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_validate_window_tag(tag)), info=True) %}
{% endmacro %}
