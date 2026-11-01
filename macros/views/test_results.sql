{#- UC5 test_results view: one row per test per invocation (spec section 7), from the node-result LOGs
    (`icecore.kind = node_result`) that icecore.emit_node_results writes (macros/telemetry/emit_node_result.sql).

    Everything reads icecore.base_events(). A node result is a test's when its dbt.node.unique_id starts with
    `test.` (singular and generic tests, and unit tests of the same prefix). Keys, as attributes.sql writes them:
    dbt.test.name / type / column / severity, dbt.result.status, dbt.result.message (redacted by default, var
    icecore.result_message), dbt.root_cause.unique_id / kind (when root-cause attribution wrote them), dbt.test.failures, dbt.result.execution_time_s and, when a scored test wrote them, the
    dbt.anomaly.* keys. A retried or duplicated record collapses to the latest one per (invocation, test).
    executed_at is the event's TIMESTAMP: OBSERVED_TIMESTAMP is NULL on icecore records (spike S3).
    model_unique_id of the spec is not a column: no emitter writes the tested model's id on the node result. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the view's SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro test_results_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
)
select
    invocation_id,
    project,
    node_unique_id as test_unique_id,
    test_name,
    test_type,
    test_column as "COLUMN",
    test_severity as severity,
    result_status as status,
    test_failures as failures,
    result_message as message,
    result_execution_time_s as execution_time_s,
    root_cause_unique_id,
    root_cause_kind,
    anomaly_engine as engine,
    anomaly_expected as expected,
    anomaly_lower as lower,
    anomaly_upper as upper,
    anomaly_score as score,
    event_at as executed_at,
    node_query_id as query_id
from events
where record_type = 'LOG'
  and attributes:"icecore.kind"::string = 'node_result'
  and node_unique_id like 'test.%'
qualify row_number() over (partition by invocation_id, node_unique_id order by event_at desc) = 1
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
