{#- UC5 capabilities view: what the account supported when icecore last looked, one row per database (spec sections 5
    and 7). It reads the `icecore.capabilities` span events that icecore.detect_capabilities records through
    icecore.emit (macros/setup/detect_capabilities.sql), keys `icecore.capability.*`: database, edition,
    edition_source, event_table, event_table_level, log_level, trace_level, cortex, cortex_reason.

    Reads icecore.base_events(). Only the latest detection of each database is a row (history stays in the event
    table). Rows without a database key are grouped under a null database. detected_at is the event's TIMESTAMP:
    OBSERVED_TIMESTAMP is NULL on icecore records (spike S3). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the view's SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro capabilities_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
)
select
    attributes:"icecore.capability.database"::string as database_name,
    event_at as detected_at,
    attributes:"icecore.capability.edition"::string as edition,
    attributes:"icecore.capability.edition_source"::string as edition_source,
    attributes:"icecore.capability.event_table"::string as event_table,
    attributes:"icecore.capability.event_table_level"::string as event_table_level,
    try_to_boolean(attributes:"icecore.capability.cortex"::string) as cortex_available,
    attributes:"icecore.capability.cortex_reason"::string as cortex_reason,
    attributes:"icecore.capability.log_level"::string as log_level,
    attributes:"icecore.capability.trace_level"::string as trace_level,
    icecore_version,
    invocation_id
from events
where record_type = 'SPAN_EVENT'
  and record_name = 'icecore.capabilities'
qualify row_number() over (partition by database_name order by event_at desc) = 1
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
