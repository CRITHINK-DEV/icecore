{#- UC6 alerts_feed view: every alertable event in one place, one row per incident (spec section 7). The Snowflake
    ALERT objects and the app read it; an acknowledged incident_key stops notifying.

    Signals, each a CTE of this view and all built from icecore.base_events() through the view SQL they come from:
      test_failure   failed_tests: test_results rows with status fail, error or warn (macros/views/test_results.sql);
      anomaly        anomalies: metrics rows scored anomalous, anomaly_is_anomaly = true (macros/views/metrics.sql);
      schema_change  schema_changes: one per column change (macros/views/schema_changes.sql);
      silence        silence_events: EMPTY SLOT, zero rows until story uc6-silence (silence_events view) fills it;
      cost           cost_regressions: EMPTY SLOT, zero rows until the cost-regression story fills it.
    A slot already has the signal columns (kind, node_key, severity, title, detected_at, invocation_id), so filling it
    is replacing its `where false` select.

    incident_key = md5(node_key | kind | window_start). The window is var icecore.alert_window_hours (default 24, a
    positive number; anything else reads as 24), aligned to the epoch: the same failure on consecutive runs inside one
    window is one incident (occurrences counts the signals), the next window is a new key and alerts again.
    node_key: test unique id; relation|metric|column|dimension; relation|column; the slots' own.

    Acknowledgements are `icecore.ack` span events written by icecore.acknowledge through ICECORE_EMIT, attributes
    icecore.ack.incident_key and icecore.ack.by. acknowledged_at / acknowledged_by are the earliest ack of the key
    (null when none). detected times are event TIMESTAMPs: OBSERVED_TIMESTAMP is NULL on icecore records (spike S3). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the view's SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro alerts_feed_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set window_seconds = icecore._icecore_alert_window_seconds() -%}
  {%- set sql -%}
with failed_tests as (
{{ icecore.test_results_view_sql(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
anomalies as (
{{ icecore.metrics_view_sql(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
schema_changes as (
{{ icecore.schema_changes_view_sql(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
-- EMPTY SLOT, filled by story uc6-silence (silence_events view): same columns as signals below.
silence_events as (
    select
        cast(null as varchar) as kind, cast(null as varchar) as node_key, cast(null as varchar) as severity,
        cast(null as varchar) as title, cast(null as timestamp_ntz) as detected_at, cast(null as varchar) as invocation_id
    where false
),
-- EMPTY SLOT, filled by the cost-regression story: same columns as signals below.
cost_regressions as (
    select
        cast(null as varchar) as kind, cast(null as varchar) as node_key, cast(null as varchar) as severity,
        cast(null as varchar) as title, cast(null as timestamp_ntz) as detected_at, cast(null as varchar) as invocation_id
    where false
),
acks as (
    select
        attributes:"icecore.ack.incident_key"::string as incident_key,
        min(event_at) as acknowledged_at,
        min_by(attributes:"icecore.ack.by"::string, event_at) as acknowledged_by
    from (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
    )
    where record_type = 'SPAN_EVENT' and record_name = 'icecore.ack'
      and attributes:"icecore.ack.incident_key"::string is not null
    group by 1
),
signals as (
    select 'test_failure' as kind, test_unique_id as node_key,
        iff(status = 'warn', 'warn', 'error') as severity,
        'test ' || coalesce(test_name, test_unique_id) || ' ' || status as title,
        executed_at as detected_at, invocation_id
    from failed_tests
    where status in ('fail', 'error', 'warn')
    union all
    select 'anomaly', relation || '|' || metric || '|' || coalesce("COLUMN", '') || '|' || coalesce(dimension, ''),
        'warn',
        'metric ' || metric || ' of ' || relation || ' is anomalous (value ' || coalesce(value::string, 'null') || ')',
        observed_at, invocation_id
    from anomalies
    where anomaly_is_anomaly = true
    union all
    select 'schema_change', relation || '|' || coalesce("COLUMN", ''), 'warn',
        'column ' || coalesce("COLUMN", '?') || ' of ' || relation || ' ' || coalesce(change_kind, 'changed'),
        detected_at, invocation_id
    from schema_changes
    union all
    select kind, node_key, severity, title, detected_at, invocation_id from silence_events
    union all
    select kind, node_key, severity, title, detected_at, invocation_id from cost_regressions
),
keyed as (
    select
        *,
        floor(date_part(epoch_second, detected_at) / {{ window_seconds }}) * {{ window_seconds }} as window_epoch
    from signals
),
incidents as (
    select
        md5(node_key || '|' || kind || '|' || window_epoch::string) as incident_key,
        kind,
        node_key,
        to_timestamp_ntz(window_epoch) as window_start,
        max_by(severity, detected_at) as severity,
        max_by(title, detected_at) as title,
        min(detected_at) as first_detected_at,
        max(detected_at) as last_detected_at,
        count(*) as occurrences,
        max_by(invocation_id, detected_at) as last_invocation_id
    from keyed
    group by node_key, kind, window_epoch
)
select
    incidents.*,
    acks.acknowledged_at,
    acks.acknowledged_by
from incidents
left join acks on acks.incident_key = incidents.incident_key
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- var icecore.alert_window_hours in seconds (integer): a positive number, default 24 hours. -#}
{% macro _icecore_alert_window_seconds() %}
  {%- set hours = icecore._icecore_setting('alert_window_hours', 24) -%}
  {%- set hours = (hours | float(0)) if hours is not none else 0 -%}
  {%- set hours = hours if hours > 0 else 24 -%}
  {{ return(((hours * 3600) | round | int) if ((hours * 3600) | round | int) > 0 else 86400) }}
{% endmacro %}
