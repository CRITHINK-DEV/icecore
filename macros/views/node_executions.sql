{#- UC5 node_executions view: one row per node per invocation (spec section 7), from the node records end_run writes
    (macros/telemetry/spans.sql, emit_node_result.sql, emit_query_event.sql), joined on dbt.invocation_id and
    dbt.node.unique_id:
      - the `icecore.node` span (ICECORE_EMIT_RUN): node type, name, materialization, relation and the node's
        dbt.node.started_at / completed_at. A node with no complete timing (skipped) has no span;
      - the node-result LOG (`icecore.kind = node_result`): dbt.result.status, rows_affected, execution_time_s,
        message and snow.query.id. Every node has one;
      - the `icecore.query` span events on the span: one snow.query.id per query the node ran.
    The row exists as soon as any of the three does. query_id is the result's snow.query.id, else the first query
    event's; query_ids lists every query id of the node.

    Times: started_at and completed_at are dbt's clock, the dbt host's (dbt.node.* attributes). The TIMESTAMP of
    the records is Snowflake's clock (the CALL's time). The two are never subtracted from each other.

    recorded_at is the TIMESTAMP of the node's result record: Snowflake's clock, the time of the CALL (null for a node that
    has a span and no result: the span's TIMESTAMP is dbt's clock and is not used). The silence
    task compares it with Snowflake's now, which keeps that comparison on one clock.

    credits and credits_settled come from the node's cost record (macros/cost/attach_cost.sql, backfill_cost.sql): the
    LOG `icecore.kind = cost`, latest per (dbt.invocation_id, dbt.node.unique_id). attach_cost writes its own LOG at
    on-run-end; backfill_cost writes one on a later run keyed to the ORIGINAL invocation and node, so the join is on
    those two keys, never on the writing run. dbt.cost.credits is the credits; a settled record (Snowflake never
    attributed the node's queries) has 0 credits and dbt.cost.settled = 'no_attribution', exposed as credits_settled
    (null for a real cost). Both are null until a cost record exists. root_cause_unique_id (dbt.root_cause.unique_id)
    is read from the node-result LOG and is null until root cause (UC4) writes it; root_cause_kind (dbt.root_cause.kind)
    comes from the same record. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro node_executions_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
node_span as (
    select * from events
    where record_type = 'SPAN' and record_name = 'icecore.node' and invocation_id is not null and node_unique_id is not null
    qualify row_number() over (partition by invocation_id, node_unique_id order by event_at desc) = 1
),
node_result as (
    select * from events
    where record_type = 'LOG' and attributes:"icecore.kind"::string = 'node_result'
      and invocation_id is not null and node_unique_id is not null
    qualify row_number() over (partition by invocation_id, node_unique_id order by event_at desc) = 1
),
node_queries as (
    select invocation_id, node_unique_id,
           array_agg(distinct node_query_id) within group (order by node_query_id) as query_ids
    from events
    where record_type = 'SPAN_EVENT' and record_name = 'icecore.query'
      and invocation_id is not null and node_unique_id is not null and node_query_id is not null
    group by invocation_id, node_unique_id
),
cost_latest as (
    select * from events
    where record_type = 'LOG' and attributes:"icecore.kind"::string = 'cost'
      and invocation_id is not null and node_unique_id is not null
    qualify row_number() over (partition by invocation_id, node_unique_id order by event_at desc) = 1
),
keys as (
    select invocation_id, node_unique_id from node_span
    union select invocation_id, node_unique_id from node_result
    union select invocation_id, node_unique_id from node_queries
)
select
    keys.invocation_id,
    keys.node_unique_id as unique_id,
    coalesce(node_span.node_type, node_result.node_type) as type,
    coalesce(node_span.node_name, node_result.node_name) as name,
    coalesce(node_span.node_materialization, node_result.node_materialization) as materialization,
    coalesce(node_span.node_relation, node_result.node_relation) as relation,
    node_result.result_status as status,
    node_result.result_message as message,
    node_result.result_rows_affected as rows_affected,
    node_result.result_execution_time_s as execution_time_s,
    coalesce(
        try_to_timestamp_ntz(node_span.attributes:"dbt.node.started_at"::string),
        try_to_timestamp_ntz(node_result.attributes:"dbt.node.started_at"::string)
    ) as started_at,
    coalesce(
        try_to_timestamp_ntz(node_span.attributes:"dbt.node.completed_at"::string),
        try_to_timestamp_ntz(node_result.attributes:"dbt.node.completed_at"::string)
    ) as completed_at,
    coalesce(node_result.node_query_id, node_queries.query_ids[0]::string) as query_id,
    node_queries.query_ids as query_ids,
    coalesce(cost_latest.cost_credits, node_result.cost_credits) as credits,
    cost_latest.attributes:"dbt.cost.settled"::string as credits_settled,
    node_result.root_cause_unique_id as root_cause_unique_id,
    node_result.event_at as recorded_at,
    coalesce(node_span.node_layer, node_result.node_layer) as layer,
    node_result.root_cause_kind as root_cause_kind
from keys
left join node_span on node_span.invocation_id = keys.invocation_id and node_span.node_unique_id = keys.node_unique_id
left join node_result on node_result.invocation_id = keys.invocation_id and node_result.node_unique_id = keys.node_unique_id
left join cost_latest on cost_latest.invocation_id = keys.invocation_id and cost_latest.node_unique_id = keys.node_unique_id
left join node_queries on node_queries.invocation_id = keys.invocation_id and node_queries.node_unique_id = keys.node_unique_id
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
