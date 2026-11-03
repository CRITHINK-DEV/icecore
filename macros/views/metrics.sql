{#- UC5 metrics view: one row per metric a monitor computed, from the `icecore.metric` span events that
    icecore.emit_metric writes (macros/monitors/emit_metric.sql; spec section 7).

    Everything reads icecore.base_events(), so the base filter applies (database, obs schema, ICECORE_EMIT*
    executables, icecore identity). Keys read exactly as emit_metric writes them: dbt.node.relation,
    dbt.metric.name / column / dimension / value and, only when a scored result was given, the six dbt.anomaly.*
    keys. The `icecore.anomaly` warn events carry the same keys as their metric event, so they are not rows here
    (one row per metric per run); alerts_feed reads them.

    observed_at is the event's TIMESTAMP: OBSERVED_TIMESTAMP is NULL on icecore records (spike S3). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the view's SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro metrics_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
)
select
    node_relation as relation,
    metric_column as "COLUMN",
    metric_dimension as dimension,
    metric_name as metric,
    event_at as observed_at,
    metric_value as value,
    anomaly_engine,
    anomaly_expected,
    anomaly_lower,
    anomaly_upper,
    anomaly_score,
    anomaly_is_anomaly,
    invocation_id,
    node_unique_id,
    emit_query_id,
    metric_threshold as threshold
from events
where record_type = 'SPAN_EVENT'
  and record_name = 'icecore.metric'
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
