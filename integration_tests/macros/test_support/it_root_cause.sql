{#- Test wrappers for the UC4 root-cause macros. Results are logged as `icecore_it_result:<json>` for pytest. -#}

{% macro it_classify_cause(node, run_results=[], signals={}) %}
  {% do log('icecore_it_result:' ~ tojson({'kind': icecore.classify_cause(node, run_results, signals)}), info=True) %}
{% endmacro %}
