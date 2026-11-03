{#- UC6 alerts_feed view: every alertable event in one place, one row per incident (spec section 7). The Snowflake
    ALERT objects and the app read it; an acknowledged incident_key stops notifying.

    Signals, each a CTE of this view and all built from icecore.base_events() through the view SQL they come from:
      test_failure   failed_tests: test_results rows with status fail, error or warn (macros/views/test_results.sql);
      anomaly        anomalies: metrics rows scored anomalous, anomaly_is_anomaly = true (macros/views/metrics.sql);
      schema_change  schema_changes: one per column change (macros/views/schema_changes.sql);
      silence        silence_events: one per missed run, the verdicts of the silence task (macros/views/silence_events.sql);
                     node_key is the node's unique id, so meta.alerts routes it like the node's other signals;
      cost           cost_regressions: METRICS rows of metric `credits` scored anomalous and above the band. Decision:
                     credits anomalies are read ONLY here (the generic anomaly signal excludes metric `credits`), so a
                     regression is one incident of kind `cost`, never two. Credits below the band raise nothing.
    Every signal CTE has the columns kind, node_key, severity, title, detected_at, invocation_id.

    incident_key = md5(node_key | kind | window_start). The window is var icecore.alert_window_hours (default 24, a
    positive number; anything else reads as 24), aligned to the epoch: the same failure on consecutive runs inside one
    window is one incident (occurrences counts the signals), the next window is a new key and alerts again.
    node_key: test unique id; relation|metric|column|dimension; relation|column; node unique id (silence); relation|credits|| (cost).

    Acknowledgements are `icecore.ack` span events written by the ICECORE_ACK procedure (icecore.acknowledge calls
    it), attributes icecore.ack.incident_key and icecore.ack.by (the caller's CURRENT_USER, recorded by the procedure).
    Only acks whose executable is ICECORE_ACK (Snowflake reports it with its signature, `ICECORE_ACK(INCIDENT_KEY VARCHAR):VARCHAR`) count (ICECORE_EMIT refuses the name; a forged one from another
    writer is ignored), and only those at or after the incident's first detection, so a key acknowledged in
    advance suppresses nothing. root_cause_unique_id / root_cause_kind (test failures only, else null) are the latest
    dbt.root_cause.* attribution of the test. acknowledged_at / acknowledged_by are the earliest such ack (null when none). detected times are event TIMESTAMPs: OBSERVED_TIMESTAMP is NULL on icecore records (spike S3). -#}


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
-- silence: one row per missed run, from the SILENCE_EVENTS view SQL (the verdicts of the silence task).
silence_events as (
    select
        'silence' as kind,
        unique_id as node_key,
        iff(lower(severity) = 'error', 'error', 'warn') as severity,
        'node ' || unique_id || ' missed its run, expected at ' || coalesce(to_char(expected_at, 'YYYY-MM-DD HH24:MI:SS'), '?') || ' UTC' as title,
        detected_at,
        cast(null as varchar) as invocation_id
    from (
{{ icecore.silence_events_view_sql(event_table=event_table, database=database, obs_schema=obs_schema) }}
    )
    where unique_id is not null and detected_at is not null
),
-- cost: credits anomalies ABOVE the band (a node that got cheaper is no regression). They are not read again by the
-- generic anomaly signal below, so a regression is one incident.
cost_regressions as (
    select
        'cost' as kind,
        relation || '|' || metric || '|' || coalesce("COLUMN", '') || '|' || coalesce(dimension, '') as node_key,
        'warn' as severity,
        'cost regression: credits of ' || relation || ' are ' || coalesce(value::string, 'null')
            || coalesce(' (band up to ' || anomaly_upper::string || ')', '') as title,
        observed_at as detected_at,
        invocation_id
    from anomalies
    where anomaly_is_anomaly = true and metric = 'credits' and coalesce(value > anomaly_upper, true)
),
acks as (
    select
        attributes:"icecore.ack.incident_key"::string as incident_key,
        event_at,
        attributes:"icecore.ack.by"::string as ack_by
    from (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
    )
    where record_type = 'SPAN_EVENT' and record_name = 'icecore.ack'
      and startswith(executable_name, 'ICECORE_ACK(')
      and attributes:"icecore.ack.incident_key"::string is not null
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
    where anomaly_is_anomaly = true and coalesce(metric, '') <> 'credits'
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
),
acknowledged as (
    select
        incidents.incident_key,
        min(acks.event_at) as acknowledged_at,
        min_by(acks.ack_by, acks.event_at) as acknowledged_by
    from incidents
    join acks on acks.incident_key = incidents.incident_key and acks.event_at >= incidents.first_detected_at
    group by incidents.incident_key
),
root_causes as (
    select
        test_unique_id as node_key,
        max_by(root_cause_unique_id, executed_at) as root_cause_unique_id,
        max_by(root_cause_kind, executed_at) as root_cause_kind
    from failed_tests
    group by test_unique_id
)
select
    incidents.incident_key,
    incidents.kind,
    incidents.node_key,
    incidents.window_start,
    incidents.severity,
    incidents.title,
    incidents.first_detected_at,
    incidents.last_detected_at,
    incidents.occurrences,
    incidents.last_invocation_id,
    acknowledged.acknowledged_at,
    acknowledged.acknowledged_by,
    root_causes.root_cause_unique_id,
    root_causes.root_cause_kind
from incidents
left join acknowledged on acknowledged.incident_key = incidents.incident_key
left join root_causes on root_causes.node_key = incidents.node_key and incidents.kind = 'test_failure'
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
