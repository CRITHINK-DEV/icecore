{#- Test wrappers for UC4 cost (macros/cost/). Results are logged as `icecore_it_result:<json>`. -#}

{% macro it_node_costs(ids_by_node, costs, settled={}, failed_ids=[]) %}
  {% do _it_result(icecore._icecore_node_costs(ids_by_node, costs, settled, failed_ids)) %}
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

{#- The items backfill_cost adds for one node, and how emit_run records them (the original invocation id must win
    over the base attribute of this run). -#}
{% macro it_backfill_items(node, cost, backfilled_by, now_ns) %}
  {% set items = icecore._icecore_backfill_items(node, cost, backfilled_by, now_ns) %}
  {% do _it_result({'items': items, 'records': icecore._icecore_emit_run_records(items),
                    'current_invocation_id': invocation_id}) %}
{% endmacro %}

{% macro it_pending_cost_sql(events_sql, days, limit, current_invocation_id) %}
  {% do _it_result(icecore._icecore_pending_cost_sql(events_sql, days, limit, current_invocation_id)) %}
{% endmacro %}

{#- Send these items to the obs schema with emit_run, as end_run would (seeds an "earlier run"). -#}
{% macro it_emit_items(items) %}
  {% do _it_result(icecore.emit_run(items)) %}
{% endmacro %}

{% macro it_settled_at_run_end(ids_by_node, found) %}
  {% do _it_result(icecore._icecore_settled_at_run_end(ids_by_node, found)) %}
{% endmacro %}

{#- Offline stand-in for the guarded attribution read: a statement that names a query id containing FAIL fails (as a
    missing grant or a timeout would), any other returns one attribution row of 0.5 credits per `q-` id it names. -#}
{% macro _it_cost_runner(sql) %}
  {%- if 'FAIL' in sql -%}{{ return({'error': 'forced read failure'}) }}{%- endif -%}
  {%- set rows = [] -%}
  {%- for query_id in modules.re.findall("'(q-[^']*)'", sql) -%}
    {%- do rows.append({'query_id': query_id, 'warehouse': 'WH', 'credits': 0.5}) -%}
  {%- endfor -%}
  {{ return({'value': rows}) }}
{% endmacro %}

{% macro it_backfill_costs_stubbed(pending, chunk_size=500) %}
  {%- set ids_by_node = {} -%}
  {%- for node in pending -%}
    {%- do ids_by_node.update({node['invocation_id'] ~ '|' ~ node['unique_id']: node['query_ids']}) -%}
  {%- endfor -%}
  {% do _it_result(icecore._icecore_backfill_costs(pending, ids_by_node, 8, chunk_size, _it_cost_runner)) %}
{% endmacro %}

{#- The metric events of a node's cost with an anomaly result, as emit_events takes them. -#}
{% macro it_cost_metric_scored(node_attrs, cost, anomaly) %}
  {% do _it_result(icecore._icecore_cost_metric_events(node_attrs, cost, anomaly)) %}
{% endmacro %}

{% macro it_backfill_items_scored(node, cost, backfilled_by, now_ns, anomaly) %}
  {% do _it_result(icecore._icecore_backfill_items(node, cost, backfilled_by, now_ns, anomaly)) %}
{% endmacro %}

{% macro it_credit_history_points_sql(keys, delay) %}
  {% do _it_result(icecore._icecore_credit_history_points_sql(keys, delay)) %}
{% endmacro %}

{#- _icecore_score_costs with a fresh batch: what it returns and the budget left (needs no connection when it scores nothing). -#}
{% macro it_score_costs(points) %}
  {% set batch = {'items': []} %}
  {% do _it_result({'scored': icecore._icecore_score_costs(points, batch), 'left': batch.get('cost_score_left')}) %}
{% endmacro %}

{#- Offline stand-in for the history count and the native scoring query of _icecore_score_costs: every key has 10 points; a
    statement that names a key containing FAIL fails; any other scores 5.0 outside the band. -#}
{% macro _it_score_runner(sql) %}
  {%- if sql.startswith('select object_agg') -%}
    {%- set counts = {} -%}
    {%- for key in modules.re.findall("'([^']*)'", sql.split('relation in (')[1]) -%}{%- do counts.update({key: 10}) -%}{%- endfor -%}
    {{ return({'value': counts}) }}
  {%- endif -%}
  {%- if 'FAIL' in sql -%}{{ return({'error': 'forced scoring failure'}) }}{%- endif -%}
  {{ return({'value': {'ENGINE': 'native', 'EXPECTED': 1.0, 'LOWER': 0.9, 'UPPER': 1.1, 'SCORE': 5.0, 'IS_ANOMALY': true,
                       'BASELINE': 'flat', 'HISTORY_POINTS': 10, 'HISTORY_DAYS': 14}}) }}
{% endmacro %}

{% macro it_score_costs_stubbed(points) %}
  {% set batch = {'items': []} %}
  {% do _it_result({'scored': icecore._icecore_score_costs(points, batch, _it_score_runner), 'left': batch.get('cost_score_left')}) %}
{% endmacro %}

{% macro it_backfill_points(pending, costs) %}
  {% do _it_result(icecore._icecore_backfill_points(pending, costs)) %}
{% endmacro %}

{% macro it_score_costs_cfg(points, cfg) %}
  {% set batch = {'items': []} %}
  {% do _it_result({'scored': icecore._icecore_score_costs(points, batch, _it_score_runner, cfg), 'left': batch.get('cost_score_left')}) %}
{% endmacro %}
