{#- UC6 silence_events view: one row per missed run (spec section 7), from the `icecore.silence` span events the
    silence task writes through ICECORE_EMIT (macros/control/render_silence_task_sql.sql). Reads icecore.base_events().

    Columns: unique_id (dbt.node.unique_id), expected_at, grace (minutes), expected_every_minutes, last_success_at,
    detected_at, channel, severity. expected_at and last_success_at are the verdict's own keys, UTC to the second,
    computed on Snowflake's clock from the TIMESTAMP of the node-result records; detected_at is the TIMESTAMP of the
    verdict record, Snowflake's clock as well. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro silence_events_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
)
select
    node_unique_id as unique_id,
    try_to_timestamp_ntz(attributes:"icecore.silence.expected_at"::string) as expected_at,
    try_to_number(attributes:"icecore.silence.grace_minutes"::string) as grace,
    try_to_number(attributes:"icecore.silence.expected_every_minutes"::string) as expected_every_minutes,
    try_to_timestamp_ntz(attributes:"icecore.silence.last_success_at"::string) as last_success_at,
    event_at as detected_at,
    attributes:"icecore.silence.channel"::string as channel,
    attributes:"icecore.silence.severity"::string as severity
from events
where record_type = 'SPAN_EVENT'
  and record_name = 'icecore.silence'
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
