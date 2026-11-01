{#- UC4 end-of-run emitter `cost` (var icecore.cost, default false): credits per node, from
    SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY, joined to the run by `snow.query.id`. Called by end_run as
    attach_cost(results, batch).

    For every node of this run that has a query id (collect_query_ids, the ids of the query events), the emitter
    reads the node's rows of QUERY_ATTRIBUTION_HISTORY through a guarded query and writes, per node whose queries
    are ALL attributed:
      - a LOG `icecore.kind = cost` in the end_run batch, with the node keys, `dbt.cost.credits` (credits attributed
        to compute plus query acceleration, summed over the node's queries) and `dbt.cost.warehouse` (the
        warehouse names, sorted, comma separated);
      - a METRIC `credits` (one `icecore.metric` span event, built by emit_metric's own builder and sent in one
        guarded CALL of ICECORE_EMIT for all nodes), so the engines can score it like any metric.
    QUERY_ATTRIBUTION_HISTORY lags by hours (Snowflake documents up to 6 hours), so the usual outcome at
    on-run-end is "not available yet": the run then logs one warning and writes nothing for those nodes;
    backfill_cost (backfill_cost.sql) fills them on a later run. A missing privilege (the role needs
    SNOWFLAKE.USAGE_VIEWER, or IMPORTED PRIVILEGES on the SNOWFLAKE database) or an empty result is the same
    outcome: a warning, never a failed run. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro attach_cost(results, batch) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set ids_by_node = {} -%}
  {%- set nodes = {} -%}
  {%- for result in results -%}
    {%- set unique_id = icecore._icecore_get(result, 'unique_id') or icecore._icecore_get(icecore._icecore_get(result, 'node'), 'unique_id') -%}
    {%- set ids = icecore.collect_query_ids(result) -%}
    {%- if unique_id and ids -%}
      {%- do ids_by_node.update({unique_id: ids}) -%}
      {%- do nodes.update({unique_id: icecore.node_attributes(icecore._icecore_get(result, 'node'))}) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if ids_by_node -%}
    {%- set found = icecore._icecore_query_costs(icecore._icecore_flat_ids(ids_by_node)) -%}
    {%- set settled = {} -%}
    {%- if icecore._icecore_cost_settled(0) -%}
      {%- for unique_id in ids_by_node -%}{%- do settled.update({unique_id: true}) -%}{%- endfor -%}
    {%- endif -%}
    {%- set costs = icecore._icecore_node_costs(ids_by_node, found['costs'], settled) -%}
    {%- set events = [] -%}
    {%- for unique_id, cost in costs.items() -%}
      {%- do batch['items'].append(icecore._icecore_cost_log(nodes[unique_id], cost)) -%}
      {%- do events.extend(icecore._icecore_cost_metric_events(nodes[unique_id], cost)) -%}
    {%- endfor -%}
    {%- if events -%}
      {%- do icecore.emit_events(events) -%}
    {%- endif -%}
    {%- if costs | length < ids_by_node | length -%}
      {%- do log('icecore: warning: cost: attribution is not available yet for ' ~ (ids_by_node | length - costs | length)
          ~ ' of ' ~ (ids_by_node | length) ~ ' nodes' ~ (' (' ~ found['error'] ~ ')' if found['error'] else '')
          ~ '; a later run backfills them (QUERY_ATTRIBUTION_HISTORY lags by hours)', info=True) -%}
    {%- endif -%}
  {%- endif -%}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The distinct query ids of {unique_id: [ids]}, in order. -#}
{% macro _icecore_flat_ids(ids_by_node) %}
  {%- set out = [] -%}
  {%- for ids in ids_by_node.values() -%}
    {%- for query_id in ids -%}
      {%- if query_id not in out -%}{%- do out.append(query_id) -%}{%- endif -%}
    {%- endfor -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- SELECT (a scalar: one array of objects) of the attribution rows for these query ids. Credits are what the
    query used for compute plus query acceleration; one row per query id. The start_time bound only prunes. -#}
{% macro _icecore_cost_lookup_sql(query_ids, lookback_days=none) %}
  {%- set days = lookback_days or icecore._icecore_number(icecore._icecore_setting('cost_lookback_days', 7)) or 7 -%}
  {%- set literals = [] -%}
  {%- for query_id in query_ids -%}
    {%- do literals.append(icecore._icecore_sql_literal(query_id)) -%}
  {%- endfor -%}
  {{ return("select array_agg(object_construct('query_id', query_id, 'warehouse', warehouse_name, "
      ~ "'credits', coalesce(credits_attributed_compute, 0) + coalesce(credits_used_query_acceleration, 0))) "
      ~ "from snowflake.account_usage.query_attribution_history "
      ~ "where start_time >= dateadd(day, -" ~ (days | int) ~ ", current_timestamp()) "
      ~ "and query_id in (" ~ (literals | join(', ')) ~ ")") }}
{% endmacro %}


{#- {costs: {query_id: {credits, warehouse}}, error: text or none}. The query runs in a block that catches every
    error (_icecore_try_scalar), 500 ids at a time; a failed chunk leaves its ids out and reports the error. -#}
{% macro _icecore_query_costs(query_ids) %}
  {%- set out = {'costs': {}, 'error': none} -%}
  {%- for start in range(0, query_ids | length, 500) -%}
    {%- set chunk = icecore._icecore_try_scalar(icecore._icecore_cost_lookup_sql(query_ids[start:start + 500])) -%}
    {%- if 'error' in chunk -%}
      {%- do out.update({'error': chunk['error']}) -%}
    {%- else -%}
      {%- for row in chunk.get('value') or [] -%}
        {%- do out['costs'].update({row['query_id']: {'credits': row['credits'], 'warehouse': row['warehouse']}}) -%}
      {%- endfor -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- {unique_id: {credits, warehouse}} for the nodes whose query ids are ALL in costs. A node with one query still
    unattributed gets nothing yet: a partial sum would be wrong, and the backfill would then add a second one.
    A node in `settled` (see _icecore_cost_settled) is closed instead: its credits are what was attributed (0 when
    nothing was) and it carries settled = 'no_attribution'. -#}
{% macro _icecore_node_costs(ids_by_node, costs, settled={}) %}
  {%- set out = {} -%}
  {%- for unique_id, ids in ids_by_node.items() -%}
    {%- set total = namespace(credits=0.0, complete=true) -%}
    {%- set warehouses = [] -%}
    {%- for query_id in ids -%}
      {%- set row = costs.get(query_id) -%}
      {%- if row is none -%}
        {%- set total.complete = false -%}
      {%- else -%}
        {%- set total.credits = total.credits + (row['credits'] or 0) -%}
        {%- if row['warehouse'] and row['warehouse'] not in warehouses -%}{%- do warehouses.append(row['warehouse']) -%}{%- endif -%}
      {%- endif -%}
    {%- endfor -%}
    {%- if total.complete -%}
      {%- do out.update({unique_id: {'credits': total.credits, 'warehouse': (warehouses | sort | join(',')) or none}}) -%}
    {%- elif settled.get(unique_id) -%}
      {#- settled: Snowflake never wrote an attribution row for this query (many statements get none), so the node
          is closed with what was attributed (0 when nothing was) and says why -#}
      {%- do out.update({unique_id: {'credits': total.credits, 'warehouse': (warehouses | sort | join(',')) or none,
                                     'settled': 'no_attribution'}}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The cost LOG for a node. node_attrs are the node's keys (node_attributes); extra overrides, such as the
    original invocation id of a backfill. -#}
{% macro _icecore_cost_log(node_attrs, cost, extra={}) %}
  {%- set attrs = {'icecore.kind': 'cost'} -%}
  {%- do attrs.update(node_attrs) -%}
  {%- do attrs.update({'dbt.cost.credits': cost['credits']}) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.cost.warehouse', cost['warehouse']) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.cost.settled', cost.get('settled')) -%}
  {%- do attrs.update(extra) -%}
  {{ return({
      'type': 'log',
      'level': 'info',
      'message': (attrs.get('dbt.node.unique_id') or 'node') ~ ' cost: ' ~ cost['credits'] ~ ' credits'
          ~ (' on ' ~ cost['warehouse'] if cost['warehouse'] else '') ~ (' (settled: ' ~ cost['settled'] ~ ')' if cost.get('settled') else ''),
      'attrs': attrs,
  }) }}
{% endmacro %}


{#- The `credits` metric of a node, as emit_events takes it, with the node's unique id added. The metric is
    keyed by the node's relation, or its unique id when it has none (a test). -#}
{% macro _icecore_cost_metric_events(node_attrs, cost) %}
  {%- if cost.get('settled') -%}{{ return([]) }}{%- endif -%}
  {%- set unique_id = node_attrs.get('dbt.node.unique_id') -%}
  {%- set events = icecore._icecore_metric_events(node_attrs.get('dbt.node.relation') or unique_id, 'credits', cost['credits']) -%}
  {%- for event in events -%}
    {%- do event['attributes'].update({'dbt.node.unique_id': unique_id}) -%}
  {%- endfor -%}
  {{ return(events) }}
{% endmacro %}


{#- var icecore.cost_settle_hours (default 24): after this age a node whose queries have no attribution row is settled
    (see _icecore_node_costs). Snowflake never writes attribution rows for many statements (views, plain table creation,
    about half of short SELECTs, measured in the sandbox), so waiting longer would leave those nodes without a cost
    forever and fill the backfill's pending set. A value of 0 or less settles at once. -#}
{% macro _icecore_cost_settle_hours() %}
  {%- set hours = icecore._icecore_number(icecore._icecore_setting('cost_settle_hours', 24)) -%}
  {{ return(24 if hours is none else hours) }}
{% endmacro %}


{#- true when a node of age_hours (hours since its run) is settled. -#}
{% macro _icecore_cost_settled(age_hours) %}
  {{ return((age_hours or 0) >= icecore._icecore_cost_settle_hours()) }}
{% endmacro %}
