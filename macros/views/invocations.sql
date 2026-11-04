{#- UC5 invocations view: one row per dbt invocation (spec section 7), from three records of the write path joined
    on dbt.invocation_id (macros/telemetry/start_run.sql, end_run.sql, spans.sql):
      - the `icecore.run_start` span event (ICECORE_EMIT, written by on-run-start): runtime, command, target,
        project, dbt.version, dbt.invocation.started_at, and icecore.layer_standard (var icecore.layer_standard, sent
        as JSON text because ICECORE_EMIT drops list values) as the array layer_standard, null when not declared;
      - the `icecore.invocation` span (ICECORE_EMIT_RUN, at on-run-end): dbt.invocation.started_at / completed_at /
        duration_s;
      - the run-end LOG (`icecore.kind = run_end`, ICECORE_EMIT_RUN): dbt.invocation.status (success, warn, error)
        and dbt.invocation.nodes_total / success / failed / warned / skipped.
    A run whose end never arrived (the process died, a hook was skipped, the CALL failed) has the start only: status
    is `running` when its start record landed less than 24 hours ago, else `unfinished`; ended_at and the counts
    are null. One row per invocation even if a record was written twice (the first start, the latest of the others).

    Times: started_at, ended_at and duration_s are dbt's clock, the dbt host's, from the dbt.invocation.* attributes.
    The event TIMESTAMP of these records is Snowflake's clock (the CALL's time). The two are never subtracted from
    each other. The 24 hour rule compares two Snowflake times (the start record's TIMESTAMP and now).

    credits is the sum of the nodes' credits (the latest cost record per invocation and node, as node_executions
    reads them, backfilled ones included), null until a cost record exists. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro invocations_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
run_start as (
    select * from events
    where record_type = 'SPAN_EVENT' and record_name = 'icecore.run_start' and invocation_id is not null
    qualify row_number() over (partition by invocation_id order by event_at) = 1
),
invocation_span as (
    select * from events
    where record_type = 'SPAN' and record_name = 'icecore.invocation' and invocation_id is not null
    qualify row_number() over (partition by invocation_id order by event_at desc) = 1
),
run_end as (
    select * from events
    where record_type = 'LOG' and attributes:"icecore.kind"::string = 'run_end' and invocation_id is not null
    qualify row_number() over (partition by invocation_id order by event_at desc) = 1
),
cost_latest as (
    select * from events
    where record_type = 'LOG' and attributes:"icecore.kind"::string = 'cost'
      and invocation_id is not null and node_unique_id is not null
    qualify row_number() over (partition by invocation_id, node_unique_id order by event_at desc) = 1
),
cost_total as (
    select invocation_id, sum(cost_credits) as credits from cost_latest group by invocation_id
),
ids as (
    select invocation_id from run_start
    union select invocation_id from invocation_span
    union select invocation_id from run_end
)
select
    ids.invocation_id,
    coalesce(run_start.runtime, invocation_span.runtime, run_end.runtime) as runtime,
    coalesce(run_start.command, invocation_span.command, run_end.command) as command,
    coalesce(run_start.target_name, invocation_span.target_name, run_end.target_name) as target,
    coalesce(run_start.project, invocation_span.project, run_end.project) as project,
    coalesce(run_start.attributes:"dbt.version"::string, run_end.attributes:"dbt.version"::string) as dbt_version,
    coalesce(
        try_to_timestamp_ntz(invocation_span.attributes:"dbt.invocation.started_at"::string),
        try_to_timestamp_ntz(run_end.attributes:"dbt.invocation.started_at"::string),
        try_to_timestamp_ntz(run_start.attributes:"dbt.invocation.started_at"::string)
    ) as started_at,
    coalesce(
        try_to_timestamp_ntz(invocation_span.attributes:"dbt.invocation.completed_at"::string),
        try_to_timestamp_ntz(run_end.attributes:"dbt.invocation.completed_at"::string)
    ) as ended_at,
    coalesce(
        try_to_double(invocation_span.attributes:"dbt.invocation.duration_s"::string),
        try_to_double(run_end.attributes:"dbt.invocation.duration_s"::string)
    ) as duration_s,
    case
        when run_end.invocation_id is not null then coalesce(run_end.attributes:"dbt.invocation.status"::string, 'unknown')
        when run_start.event_at > dateadd(hour, -24, convert_timezone('UTC', current_timestamp())::timestamp_ntz) then 'running'
        else 'unfinished'
    end as status,
    try_to_number(run_end.attributes:"dbt.invocation.nodes_total"::string) as nodes_total,
    try_to_number(run_end.attributes:"dbt.invocation.nodes_success"::string) as nodes_success,
    try_to_number(run_end.attributes:"dbt.invocation.nodes_failed"::string) as nodes_failed,
    try_to_number(run_end.attributes:"dbt.invocation.nodes_warned"::string) as nodes_warned,
    try_to_number(run_end.attributes:"dbt.invocation.nodes_skipped"::string) as nodes_skipped,
    cost_total.credits as credits,
    coalesce(as_array(try_parse_json(as_varchar(run_start.attributes:"icecore.layer_standard"))),
             as_array(run_start.attributes:"icecore.layer_standard")) as layer_standard
from ids
left join run_start on run_start.invocation_id = ids.invocation_id
left join invocation_span on invocation_span.invocation_id = ids.invocation_id
left join run_end on run_end.invocation_id = ids.invocation_id
left join cost_total on cost_total.invocation_id = ids.invocation_id
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
