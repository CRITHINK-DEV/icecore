{#- Test wrapper: logs the result of icecore.classify_cause as JSON for the pytest harness. -#}
{% macro it_classify_cause(node, run_results=[], signals={}) %}
  {% set kind = icecore.classify_cause(node, run_results, signals) %}
  {% do log('ICECORE_RESULT ' ~ tojson({'kind': kind}), info=True) %}
{% endmacro %}
