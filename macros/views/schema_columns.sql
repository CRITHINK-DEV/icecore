{#- UC5/UC8 schema_columns view: the full column state of a relation, one row per column per schema_changes run.

    Reads icecore.base_events(). The schema_changes test writes the current columns on every run as
    `icecore.schema_signature` span events, one per chunk of columns (a wide table is split under the payload cap):
      dbt.node.relation          the relation
      dbt.schema.columns         JSON array of {name, data_type}: this chunk's columns
      dbt.schema.chunk_index     0-based position of the chunk (records written before chunking have none: chunk 0)
    A snapshot is the chunks of one invocation for one relation; a repeat of a chunk in the same invocation (two tests
    on one relation) counts once. Columns: relation, "COLUMN", data_type, snapshot_at (the first record of the
    snapshot: the same for all its columns), is_new (the relation had a previous snapshot and the column is not in it,
    as in Elementary: false on the first snapshot), invocation_id. Only complete snapshots are listed (all chunk_count
    chunks landed). -#}


{#- SQL text of the view's SELECT. Without event_table it is resolved when the macro executes. -#}
{% macro schema_columns_view_sql(event_table=none, database=none, obs_schema=none) %}
  {%- set sql -%}
with events as (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=obs_schema) }}
),
chunks as (
    select node_relation as relation, invocation_id, event_at,
           coalesce(attributes:"dbt.schema.chunk_count"::int, 1) as chunk_count,
           parse_json(attributes:"dbt.schema.columns"::string) as cols
    from events
    where record_type = 'SPAN_EVENT'
      and record_name = 'icecore.schema_signature'
    qualify row_number() over (partition by node_relation, invocation_id,
                                            coalesce(attributes:"dbt.schema.chunk_index"::int, 0)
                               order by event_at desc) = 1
),
snapshots as (
    -- only complete snapshots: a chunk not landed yet must not show its columns as removed
    select relation, invocation_id, min(event_at) as snapshot_at,
           lag(invocation_id) over (partition by relation order by min(event_at), invocation_id) as previous_invocation_id
    from chunks
    group by relation, invocation_id
    having count(*) = max(chunk_count)
),
current_columns as (
    select c.relation, c.invocation_id, f.value:name::string as column_name, f.value:data_type::string as data_type
    from chunks c, lateral flatten(input => c.cols) f
    qualify row_number() over (partition by c.relation, c.invocation_id, f.value:name::string order by c.event_at desc) = 1
)
select
    cc.relation as relation,
    cc.column_name as "COLUMN",
    cc.data_type as data_type,
    s.snapshot_at as snapshot_at,
    s.previous_invocation_id is not null and previous.column_name is null as is_new,
    cc.invocation_id as invocation_id
from current_columns cc
join snapshots s on s.relation = cc.relation and s.invocation_id = cc.invocation_id
left join current_columns previous on previous.relation = cc.relation
    and previous.invocation_id = s.previous_invocation_id and previous.column_name = cc.column_name
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}
