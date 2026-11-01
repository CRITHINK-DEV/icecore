{#- UC5 schema_changes view: one row per detected column change (spec section 7).

    Reads icecore.base_events(). Nothing emits schema-change records yet (macros/monitors/schema.sql only returns
    diff_schema's values; emitting is the monitors' job, a later story), so the keys are the spec's (section 4
    namespace, section 7 columns), and the emitting story must write exactly these: a span event named
    `icecore.schema_change` (icecore.emit_events kind `schema_change`) with attributes
      dbt.node.relation          the relation the change is on
      dbt.schema.change_kind     added | removed | type_changed
      dbt.schema.column          the column name
      dbt.schema.from_type       the previous type (null for added)
      dbt.schema.to_type         the new type (null for removed)
    detected_at is the event's TIMESTAMP. A row with any of these keys missing reads that column as null. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the view's SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro schema_changes_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
)
select
    node_relation as relation,
    attributes:"dbt.schema.change_kind"::string as change_kind,
    attributes:"dbt.schema.column"::string as "COLUMN",
    attributes:"dbt.schema.from_type"::string as from_type,
    attributes:"dbt.schema.to_type"::string as to_type,
    event_at as detected_at,
    invocation_id,
    node_unique_id,
    emit_query_id
from events
where record_type = 'SPAN_EVENT'
  and record_name = 'icecore.schema_change'
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
