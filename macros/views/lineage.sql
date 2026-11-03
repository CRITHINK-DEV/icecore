{#- UC5 lineage views: the DAG edges, from the LOG records `icecore.kind = lineage` that icecore.emit_lineage writes
    (branch story/uc1-lineage, macros/telemetry/emit_lineage.sql, matched at commit c793437).

    A record carries `dbt.lineage.edges`, a JSON list of [parent_unique_id, child_unique_id] pairs, and a run's edges
    may be split over several records (dbt.lineage.part / parts); the views flatten the lists, so one row is one edge
    whatever the split. Two views, both over icecore.base_events():
      LINEAGE         one row per edge per invocation (a repeated edge in one invocation collapses to one row);
      LINEAGE_LATEST  the edge set of the latest invocation that emitted lineage, per dbt project.
    parent_layer and child_layer are the layers of the edge's two nodes, read from `dbt.lineage.node_layer` (a JSON
    object {unique_id: layer} on the record); null for a record written before that key existed.
    recorded_at is the event's TIMESTAMP: OBSERVED_TIMESTAMP is NULL on icecore records (spike S3). -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the LINEAGE SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro lineage_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
{{ icecore._icecore_lineage_edges_cte(event_table, database, obs_schema) }}
select
    invocation_id,
    project,
    parent_unique_id,
    child_unique_id,
    recorded_at,
    parent_layer,
    child_layer
from edges
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- SQL text of the LINEAGE_LATEST SELECT: the same edges, only those of each project's latest invocation. -#}
{% macro lineage_latest_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
{{ icecore._icecore_lineage_edges_cte(event_table, database, obs_schema) }}
select
    invocation_id,
    project,
    parent_unique_id,
    child_unique_id,
    recorded_at,
    parent_layer,
    child_layer
from edges
qualify dense_rank() over (partition by project order by invocation_at desc, invocation_id desc) = 1
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- `with events as (...), edges as (...)`: edges is one row per (invocation, parent, child), with the time of the
    invocation's last lineage record as invocation_at. -#}
{% macro _icecore_lineage_edges_cte(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
flat as (
    select
        events.invocation_id,
        events.project,
        events.event_at,
        edge.value[0]::string as parent_unique_id,
        edge.value[1]::string as child_unique_id,
        get(try_parse_json(events.attributes:"dbt.lineage.node_layer"::string), edge.value[0]::string)::string as parent_layer,
        get(try_parse_json(events.attributes:"dbt.lineage.node_layer"::string), edge.value[1]::string)::string as child_layer
    from events,
        lateral flatten(input => try_parse_json(events.attributes:"dbt.lineage.edges"::string)) as edge
    where events.record_type = 'LOG'
      and events.attributes:"icecore.kind"::string = 'lineage'
),
edges as (
    select
        invocation_id,
        project,
        parent_unique_id,
        child_unique_id,
        max(event_at) as recorded_at,
        max(parent_layer) as parent_layer,
        max(child_layer) as child_layer,
        max(max(event_at)) over (partition by invocation_id) as invocation_at
    from flat
    where parent_unique_id is not null and child_unique_id is not null
    group by invocation_id, project, parent_unique_id, child_unique_id
)
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
