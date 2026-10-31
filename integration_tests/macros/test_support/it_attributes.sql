{#- Test wrappers for the UC1 attribute namespace. Results are logged as `icecore_it_result:<json>` for pytest. -#}

{% macro it_base_attributes() %}
  {% do log('icecore_it_result:' ~ tojson(icecore.base_attributes()), info=True) %}
{% endmacro %}

{#- Either unique_id (looked up in the graph: nodes, then sources) or a node dict; result is an optional run result dict. -#}
{% macro it_node_attributes(unique_id=none, node=none, result=none) %}
  {% if unique_id is not none %}
    {% set node = graph.nodes.get(unique_id) or graph.sources.get(unique_id) %}
  {% endif %}
  {% do log('icecore_it_result:' ~ tojson(icecore.node_attributes(node, result)), info=True) %}
{% endmacro %}
