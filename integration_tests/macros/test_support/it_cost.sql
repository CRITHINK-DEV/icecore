{#- Test wrappers for UC4 cost (macros/cost/). Results are logged as `icecore_it_result:<json>`. -#}

{% macro it_node_costs(ids_by_node, costs, settled={}) %}
  {% do _it_result(icecore._icecore_node_costs(ids_by_node, costs, settled)) %}
{% endmacro %}

{#- The cost LOG and the metric events for one node, as attach_cost builds them. -#}
{% macro it_cost_items(node_attrs, cost) %}
  {% do _it_result({'log': icecore._icecore_cost_log(node_attrs, cost),
                    'metric': icecore._icecore_cost_metric_events(node_attrs, cost)}) %}
{% endmacro %}

{% macro it_cost_lookup_sql(query_ids) %}
  {% do _it_result(icecore._icecore_cost_lookup_sql(query_ids)) %}
{% endmacro %}

{#- attach_cost over these results, then what end_run would send: the batch is sent with emit_run to the obs schema.
    Returns the invocation id and the items. -#}
{% macro it_attach_cost_run(results) %}
  {% set batch = {'items': [], 'node_spans': {}, 'timing': {}} %}
  {% do icecore.attach_cost(results, batch) %}
  {% do icecore.emit_run(batch['items']) %}
  {% do _it_result({'invocation_id': invocation_id, 'items': batch['items']}) %}
{% endmacro %}

{% macro it_cost_settled(age_hours) %}
  {% do _it_result({'settled': icecore._icecore_cost_settled(age_hours), 'hours': icecore._icecore_cost_settle_hours()}) %}
{% endmacro %}

{% macro it_settled_at_run_end(ids_by_node, found) %}
  {% do _it_result(icecore._icecore_settled_at_run_end(ids_by_node, found)) %}
{% endmacro %}
