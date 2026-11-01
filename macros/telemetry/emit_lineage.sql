{#- UC1 end-of-run emitter `lineage` (var icecore.lineage, default true): the project's DAG edges as LOG records,
    `icecore.kind = lineage`. Each edge is a [parent, child] pair of unique ids read from graph.nodes depends_on,
    between models, seeds, snapshots and sources (tests are left out: a test is not a lineage edge). The whole
    current graph is sent every run, not only the selected nodes. `dbt.lineage.edges` is a JSON list of pairs,
    sorted. Edges that do not fit payload_cap_kb are split across as many records as needed, in order, with
    `dbt.lineage.part` / `dbt.lineage.parts`, `dbt.lineage.edge_count` (this record) and `dbt.lineage.edges_total`;
    no edge is dropped and no record is truncated. A graph without edges adds no record. Called by end_run as
    emit_lineage(results, batch). -#}
{% macro emit_lineage(results, batch) %}
  {%- do icecore._icecore_lineage_append(icecore._icecore_lineage_edges(graph), batch) -%}
{% endmacro %}


{#- Sorted, de-duplicated [parent, child] pairs of the graph. -#}
{% macro _icecore_lineage_edges(graph) %}
  {%- set seen = {} -%}
  {%- for node in (graph.nodes or {}).values() -%}
    {%- set child = node['unique_id'] -%}
    {%- if (child | string).startswith(('model.', 'seed.', 'snapshot.')) -%}
      {%- for parent in (icecore._icecore_get(icecore._icecore_get(node, 'depends_on'), 'nodes') or []) -%}
        {%- if (parent | string).startswith(('model.', 'seed.', 'snapshot.', 'source.')) -%}
          {%- do seen.update({parent ~ ' ' ~ child: [parent, child]}) -%}
        {%- endif -%}
      {%- endfor -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(seen.values() | list | sort) }}
{% endmacro %}


{#- The edges split into parts: as few as fit under the payload cap, in order. One count for the log, the row counts and the tests. -#}
{% macro _icecore_lineage_parts(edges) %}
  {%- set parts = [] -%}
  {%- if edges -%}
    {#- room for the edges: the cap less the base attributes and this record's own attributes and message -#}
    {%- set room = icecore._icecore_payload_cap_bytes() - (tojson(icecore.base_attributes()) | length) - 700 -%}
    {%- set ns = namespace(current=[], size=0) -%}
    {%- for edge in edges -%}
      {#- the pair as it sits in the JSON string attribute, which JSON escapes once more, plus the comma -#}
      {%- set size = (tojson(tojson(edge)) | length) + 1 -%}
      {%- if ns.current and ns.size + size > room -%}
        {%- do parts.append(ns.current) -%}
        {%- set ns.current = [] -%}
        {%- set ns.size = 0 -%}
      {%- endif -%}
      {%- do ns.current.append(edge) -%}
      {%- set ns.size = ns.size + size -%}
    {%- endfor -%}
    {%- do parts.append(ns.current) -%}
  {%- endif -%}
  {{ return(parts) }}
{% endmacro %}


{#- Add the LOG items for these edges to batch['items']. -#}
{% macro _icecore_lineage_append(edges, batch) %}
  {%- set parts = icecore._icecore_lineage_parts(edges) -%}
  {%- for part in parts -%}
    {%- do batch['items'].append({
        'type': 'log',
        'level': 'info',
        'message': 'dbt lineage ' ~ loop.index ~ '/' ~ (parts | length) ~ ': ' ~ (part | length) ~ ' of ' ~ (edges | length) ~ ' edges',
        'attrs': {
            'icecore.kind': 'lineage',
            'dbt.lineage.edges': tojson(part),
            'dbt.lineage.edge_count': part | length,
            'dbt.lineage.edges_total': edges | length,
            'dbt.lineage.part': loop.index,
            'dbt.lineage.parts': parts | length,
        },
    }) -%}
  {%- endfor -%}
{% endmacro %}
