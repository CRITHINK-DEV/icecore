{#- Test wrapper: logs the result of icecore.walk_upstream as JSON for the pytest harness. -#}
{% macro it_walk_upstream(unique_id, results=[], signals={}) %}
  {% set found = icecore.walk_upstream(unique_id, results, signals) %}
  {% do log('ICECORE_RESULT ' ~ tojson({'cause': found}), info=True) %}
{% endmacro %}
