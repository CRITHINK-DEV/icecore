{#- UC6 dedup_window: what an alert may still notify (spec sections 5 and 8). Suppression is computed over the
    ALERTS_FEED view plus a record of what was notified; nothing is stored in a table.

    The record of a notification is a span event `icecore.notified`, attributes icecore.notified.incident_key and
    icecore.notified.channel, written through ICECORE_EMIT by the alert's own action (macros/control/render_alert_sql.sql;
    never an INSERT). dedup_window_sql(channel) is the SELECT of the rows that channel's alert may notify now:
      - an incident acknowledged in the feed (acknowledged_at set) is never notified, however long ago or often it
        recurs;
      - an incident already notified on this channel inside the window is not notified again;
      - an incident last notified on this channel more than the window ago, and still unacknowledged, is notified again
        (a reminder);
      - a new incident_key (a recurrence in a new alert window, or after an acknowledgement, a different node, kind or
        window) has no notification record and is notified.
    A notification on one channel does not suppress another channel. The window is var icecore.alert_dedup_minutes
    (a positive number); by default it is the incident window (var icecore.alert_window_hours, 24 hours), so an
    incident is notified once per channel. Times are event TIMESTAMPs (UTC). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the SELECT: the ALERTS_FEED columns of the rows to notify, plus last_notified_at (null when never
    notified on this channel). schema is the obs schema holding ALERTS_FEED, which is also the schema the base filter
    reads. Without event_table it is resolved when the macro executes. -#}
{% macro dedup_window_sql(channel, schema=none, event_table=none, database=none, window_minutes=none) %}
  {%- set window = window_minutes if window_minutes is not none else icecore.dedup_window_minutes() -%}
  {%- set sql -%}
with notified as (
    select
        attributes:"icecore.notified.incident_key"::string as incident_key,
        attributes:"icecore.notified.channel"::string as channel,
        max(event_at) as last_notified_at
    from (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=icecore.obs_schema_name(schema)) }}
    )
    where record_type = 'SPAN_EVENT' and record_name = 'icecore.notified'
    group by 1, 2
)
select
    feed.*,
    notified.last_notified_at
from {{ icecore._icecore_procedure('ALERTS_FEED', schema) }} as feed
left join notified
    on notified.incident_key = feed.incident_key
   and notified.channel = {{ icecore._icecore_sql_literal(channel | string) }}
where feed.acknowledged_at is null
  and (notified.last_notified_at is null
       or notified.last_notified_at < dateadd(minute, -{{ window }}, convert_timezone('UTC', current_timestamp())::timestamp_ntz))
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- The dedup window in whole minutes: var icecore.alert_dedup_minutes when a positive number, else the incident
    window of alerts_feed (24 hours by default). -#}
{% macro dedup_window_minutes() %}
  {%- set minutes = icecore._icecore_setting('alert_dedup_minutes', none) -%}
  {%- set minutes = (minutes | float(0)) if minutes is not none and minutes is not mapping else 0 -%}
  {%- if minutes > 0 -%}
    {{ return([minutes | round | int, 1] | max) }}
  {%- endif -%}
  {{ return((icecore._icecore_alert_window_seconds() / 60) | round | int) }}
{% endmacro %}
