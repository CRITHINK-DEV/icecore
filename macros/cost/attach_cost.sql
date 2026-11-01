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
    outcome: a warning, never a failed run.

    The same call then runs backfill_cost (below): nodes of EARLIER runs that have no cost record yet and whose
    attribution is available now get theirs, keyed to their original invocation. -#}


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
    {%- set settled = icecore._icecore_settled_at_run_end(ids_by_node, found) -%}
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
  {%- do icecore.backfill_cost(batch) -%}
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
{% macro _icecore_query_costs(query_ids, lookback_days=none) %}
  {%- set out = {'costs': {}, 'error': none} -%}
  {%- for start in range(0, query_ids | length, 500) -%}
    {%- set chunk = icecore._icecore_try_scalar(icecore._icecore_cost_lookup_sql(query_ids[start:start + 500], lookback_days)) -%}
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


{#- {unique_id: true} of the nodes settled at once (cost_settle_hours <= 0): none when the attribution read failed,
    because a failed read says nothing about Snowflake never attributing the queries. -#}
{% macro _icecore_settled_at_run_end(ids_by_node, found) %}
  {%- set settled = {} -%}
  {%- if not found['error'] and icecore._icecore_cost_settled(0) -%}
    {%- for unique_id in ids_by_node -%}{%- do settled.update({unique_id: true}) -%}{%- endfor -%}
  {%- endif -%}
  {{ return(settled) }}
{% endmacro %}


{#- true when a node of age_hours (hours since its run) is settled. -#}
{% macro _icecore_cost_settled(age_hours) %}
  {{ return((age_hours or 0) >= icecore._icecore_cost_settle_hours()) }}
{% endmacro %}


{#- Backfill ---------------------------------------------------------------------------------------- -#}

{#- Called by attach_cost (so behind var cost) after the run's own nodes. Finds the nodes of earlier invocations
    (the last cost_lookback_days, default 7, at most cost_backfill_limit nodes per run, default 500) that have
    query ids in the event table and no cost record, reads their attribution, and adds to the batch, for each node
    whose queries are ALL attributed now:
      - the same cost LOG as attach_cost, with EXPLICIT `dbt.invocation_id` = the original invocation (the
        base attribute of the current run is overridden), `dbt.node.unique_id`, `icecore.backfill = true` and
        `icecore.backfilled_by` = the current invocation id;
      - a span `icecore.cost_backfill` carrying the METRIC `credits` as an `icecore.metric` span event, also with
        the original invocation id, written at the time the cost became known.
    How to join a backfilled cost back to its node: on (`invocation_id`, `node_unique_id`), never on `command` or
    `target`, which describe the run that backfilled it. node_executions takes `credits` as the maximum of
    `cost_credits` over the rows of its (invocation_id, node_unique_id).
    Time: the cost LOG and the metric are written when the cost becomes known, so their TIMESTAMP is the backfilling
    run's, hours after the node ran. The node's own time rides along as `dbt.cost.node_at` (its first record's time,
    ISO-8601 UTC). The history reader orders a series by the METRICS view's observed_at, which is the write time, so a
    backfilled `credits` point sits at the time the cost was known, not when the node ran: a documented drift of the
    attribution lag (hours), the same for every point of the series.
    A node older than cost_settle_hours (default 24) whose queries still have no attribution row is settled at what
    was attributed (0 when nothing was), `dbt.cost.settled = no_attribution`, with no metric: that record also takes
    the node out of the pending set, so statements Snowflake never attributes do not fill the cost_backfill_limit.
    No duplicates: a node with a cost record of any invocation in the window (written by attach_cost or by an
    earlier backfill) is left out, checked through icecore.base_events(). A record written less than the ingestion
    lag (about 20 s, S3) ago is not seen yet; the backfill of runs that start within that lag can write it twice.
    Everything is guarded: no active event table, an unreadable event table or attribution view, or nothing
    pending is a warning or silence, never a failed run. -#}
{% macro backfill_cost(batch) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set pending = icecore._icecore_pending_cost_nodes() -%}
  {%- if not pending -%}{{ return(none) }}{%- endif -%}
  {%- set ids_by_node = {} -%}
  {%- for node in pending -%}
    {%- do ids_by_node.update({node['invocation_id'] ~ '|' ~ node['unique_id']: node['query_ids']}) -%}
  {%- endfor -%}
  {%- set days = (icecore._icecore_number(icecore._icecore_setting('cost_lookback_days', 7)) or 7) | int -%}
  {%- set found = icecore._icecore_query_costs(icecore._icecore_flat_ids(ids_by_node), days + 1) -%}
  {%- set settled = {} -%}
  {%- for node in pending -%}
    {%- if icecore._icecore_cost_settled(node.get('age_hours')) -%}
      {%- do settled.update({node['invocation_id'] ~ '|' ~ node['unique_id']: true}) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set costs = icecore._icecore_node_costs(ids_by_node, found['costs'], settled) -%}
  {%- set now_ns = (batch.get('timing') or {}).get('completed_ns') or icecore._icecore_epoch_ns(modules.datetime.datetime.utcnow()) -%}
  {%- for node in pending -%}
    {%- set cost = costs.get(node['invocation_id'] ~ '|' ~ node['unique_id']) -%}
    {%- if cost is not none -%}
      {%- do batch['items'].extend(icecore._icecore_backfill_items(node, cost, invocation_id, now_ns)) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if found['error'] -%}
    {%- do log('icecore: warning: cost backfill: attribution could not be read (' ~ found['error'] ~ ')', info=True) -%}
  {%- endif -%}
  {%- if costs -%}
    {%- do log('icecore: cost backfill: ' ~ (costs | length) ~ ' of ' ~ (pending | length) ~ ' earlier nodes got their cost', info=True) -%}
  {%- endif -%}
{% endmacro %}


{#- The cost LOG and the metric span of one backfilled node, both on the original invocation. -#}
{% macro _icecore_backfill_items(node, cost, backfilled_by, now_ns) %}
  {%- set attrs = {} -%}
  {%- for key, name in [('unique_id', 'dbt.node.unique_id'), ('type', 'dbt.node.type'), ('name', 'dbt.node.name'),
                        ('materialization', 'dbt.node.materialization'), ('relation', 'dbt.node.relation')] -%}
    {%- do icecore._icecore_put(attrs, name, node.get(key)) -%}
  {%- endfor -%}
  {%- set explicit = {'dbt.invocation_id': node['invocation_id'], 'icecore.backfill': true,
                      'icecore.backfilled_by': backfilled_by} -%}
  {%- do icecore._icecore_put(explicit, 'dbt.cost.node_at', node.get('node_at')) -%}
  {%- set span_events = [] -%}
  {%- for event in icecore._icecore_cost_metric_events(attrs, cost) -%}
    {%- set event_attrs = {'icecore.level': event['level']} -%}
    {%- do event_attrs.update(event['attributes']) -%}
    {%- do event_attrs.update(explicit) -%}
    {%- do span_events.append({'name': 'icecore.' ~ event['kind'], 'ts_ns': now_ns, 'attrs': event_attrs}) -%}
  {%- endfor -%}
  {%- set span_attrs = {} -%}
  {%- do span_attrs.update(attrs) -%}
  {%- do span_attrs.update(explicit) -%}
  {%- set items = [icecore._icecore_cost_log(attrs, cost, explicit)] -%}
  {%- if span_events -%}
    {%- do items.append({'name': 'icecore.cost_backfill', 'start_ns': now_ns, 'end_ns': now_ns, 'attrs': span_attrs, 'events': span_events}) -%}
  {%- endif -%}
  {{ return(items) }}
{% endmacro %}


{#- The pending nodes: [{invocation_id, unique_id, type, name, materialization, relation, query_ids}], oldest first.
    The SELECT reads base_events (record attributes, including the node results' and query events' snow.query.id);
    a node is pending when it has a query id and no row with cost_credits in the window. -#}
{% macro _icecore_pending_cost_nodes() %}
  {%- set shown = icecore._icecore_try_show("show parameters like 'EVENT_TABLE' in database " ~ icecore._icecore_identifier(target.database)) -%}
  {%- if 'error' in shown -%}
    {%- do log('icecore: warning: cost backfill: the event table could not be looked up (' ~ shown['error'] ~ ')', info=True) -%}
    {{ return([]) }}
  {%- endif -%}
  {%- set rows = shown.get('rows') or [] -%}
  {%- set table = icecore.event_table_to_read(rows[0]['value'] if rows | length > 0 else none) -%}
  {%- if table is none -%}
    {%- do log('icecore: warning: cost backfill: the database has no active event table, nothing to backfill', info=True) -%}
    {{ return([]) }}
  {%- endif -%}
  {%- set days = (icecore._icecore_number(icecore._icecore_setting('cost_lookback_days', 7)) or 7) | int -%}
  {%- set limit = (icecore._icecore_number(icecore._icecore_setting('cost_backfill_limit', 500)) or 500) | int -%}
  {%- set out = icecore._icecore_try_scalar(icecore._icecore_pending_cost_sql(icecore.base_events(event_table=table), days, limit, invocation_id)) -%}
  {%- if 'error' in out -%}
    {%- do log('icecore: warning: cost backfill: the event table could not be read (' ~ out['error'] ~ ')', info=True) -%}
    {{ return([]) }}
  {%- endif -%}
  {{ return(out.get('value') or []) }}
{% endmacro %}


{#- The SELECT (one array of objects) behind _icecore_pending_cost_nodes, over `events_sql` (base_events).
    The current invocation is left out: attach_cost has just tried it. -#}
{% macro _icecore_pending_cost_sql(events_sql, days, limit, current_invocation_id) %}
  {%- set sql -%}
with events as (
{{ events_sql }}
),
window_events as (
  select * from events
  where event_at >= dateadd(day, -{{ days | int }}, current_timestamp())
    and node_unique_id is not null and invocation_id is not null
    and invocation_id <> {{ icecore._icecore_sql_literal(current_invocation_id) }}
),
costed as (
  select distinct invocation_id, node_unique_id from window_events where cost_credits is not null
),
pending as (
  select w.invocation_id, w.node_unique_id,
         max(w.node_type) as node_type, max(w.node_name) as node_name,
         max(w.node_materialization) as node_materialization, max(w.node_relation) as node_relation,
         array_agg(distinct w.node_query_id) as query_ids, min(w.event_at) as first_at,
         datediff('hour', min(w.event_at), convert_timezone('UTC', current_timestamp())::timestamp_ntz) as age_hours
  from window_events w
  left join costed c on c.invocation_id = w.invocation_id and c.node_unique_id = w.node_unique_id
  where c.node_unique_id is null
  group by w.invocation_id, w.node_unique_id
  having array_size(array_agg(distinct w.node_query_id)) > 0
  order by min(w.event_at)
  limit {{ limit | int }}
)
select array_agg(object_construct('invocation_id', invocation_id, 'unique_id', node_unique_id, 'type', node_type,
       'name', node_name, 'materialization', node_materialization, 'relation', node_relation,
       'query_ids', query_ids, 'age_hours', age_hours,
       'node_at', to_char(first_at, 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"')))
from pending
  {%- endset -%}
  {{ return(sql | trim) }}
{% endmacro %}
