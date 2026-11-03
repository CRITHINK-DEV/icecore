{#- UC1 end-of-run emitter `lineage` (var icecore.lineage, default true): the project's DAG edges as LOG records,
    `icecore.kind = lineage`. Each edge is a [parent, child] pair of unique ids read from graph.nodes depends_on,
    between models, seeds, snapshots and sources (tests are left out: a test is not a lineage edge). The whole
    current graph is sent every run, not only the selected nodes. `dbt.lineage.edges` is a JSON list of pairs,
    sorted. Edges that do not fit payload_cap_kb are split across as many records as needed, in order, with
    `dbt.lineage.part` / `dbt.lineage.parts`, `dbt.lineage.edge_count` (this record) and `dbt.lineage.edges_total`;
    no edge is dropped and no record is truncated. Each record also carries, for the nodes of its edges that the graph
    knows, `dbt.lineage.node_fqn` (JSON object {unique_id: dotted fqn}) and `dbt.lineage.node_layer` ({unique_id: layer},
    see _icecore_layer). A graph without edges adds no record. Called by end_run as
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


{#- The nodes of the edges that the graph knows: {unique_id: {fqn, layer}} (layer left out when none). Sources are
    read from graph.sources. A node the graph does not know (a synthetic edge) is left out. -#}
{% macro _icecore_lineage_node_info(edges) %}
  {%- set info = {} -%}
  {%- for edge in edges -%}
    {%- for unique_id in edge -%}
      {%- if unique_id not in info -%}
        {%- set node = (graph.nodes or {}).get(unique_id) or (graph.sources or {}).get(unique_id) -%}
        {%- if node -%}
          {%- set fqn = icecore._icecore_node_fqn(icecore._icecore_get(node, 'fqn')) -%}
          {%- set entry = {} -%}
          {%- do icecore._icecore_put(entry, 'fqn', fqn) -%}
          {%- do icecore._icecore_put(entry, 'layer', icecore._icecore_layer(unique_id.split('.')[0], fqn)) -%}
          {%- do info.update({unique_id: entry}) -%}
        {%- endif -%}
      {%- endif -%}
    {%- endfor -%}
  {%- endfor -%}
  {{ return(info) }}
{% endmacro %}


{#- {unique_id: value} of one key of node info, for the nodes of these edges that have it. -#}
{% macro _icecore_lineage_node_map(edges, info, key) %}
  {%- set out = {} -%}
  {%- for edge in edges -%}
    {%- for unique_id in edge -%}
      {%- if unique_id in info and key in info[unique_id] -%}
        {%- do out.update({unique_id: info[unique_id][key]}) -%}
      {%- endif -%}
    {%- endfor -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The edges split into parts: as few as fit under the payload cap, in order. One count for the log, the row counts and the tests.
    Each edge counts its two nodes' fqn and layer entries too (written once per record, counted per edge: a safe over-count). -#}
{% macro _icecore_lineage_parts(edges, info=none) %}
  {%- set info = info if info is not none else icecore._icecore_lineage_node_info(edges) -%}
  {%- set parts = [] -%}
  {%- if edges -%}
    {#- room for the edges: the cap less the base attributes and this record's own attributes and message -#}
    {%- set room = icecore._icecore_payload_cap_bytes() - (tojson(icecore.base_attributes()) | length) - 700 -%}
    {%- set ns = namespace(current=[], size=0) -%}
    {%- for edge in edges -%}
      {#- the pair as it sits in the JSON string attribute, which JSON escapes once more, plus the comma -#}
      {%- set extra = namespace(size=0) -%}
      {%- for unique_id in edge -%}
        {%- if unique_id in info -%}
          {%- set extra.size = extra.size + (tojson(tojson({unique_id: info[unique_id]})) | length) -%}
        {%- endif -%}
      {%- endfor -%}
      {%- set size = (tojson(tojson(edge)) | length) + 1 + extra.size -%}
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
  {%- set info = icecore._icecore_lineage_node_info(edges) -%}
  {%- set parts = icecore._icecore_lineage_parts(edges, info) -%}
  {%- for part in parts -%}
    {%- set attrs = {
            'icecore.kind': 'lineage',
            'dbt.lineage.edges': tojson(part),
            'dbt.lineage.edge_count': part | length,
            'dbt.lineage.edges_total': edges | length,
            'dbt.lineage.part': loop.index,
            'dbt.lineage.parts': parts | length,
    } -%}
    {%- set fqns = icecore._icecore_lineage_node_map(part, info, 'fqn') -%}
    {%- set layers = icecore._icecore_lineage_node_map(part, info, 'layer') -%}
    {%- if fqns -%}{%- do attrs.update({'dbt.lineage.node_fqn': tojson(fqns)}) -%}{%- endif -%}
    {%- if layers -%}{%- do attrs.update({'dbt.lineage.node_layer': tojson(layers)}) -%}{%- endif -%}
    {%- do batch['items'].append({
        'type': 'log',
        'level': 'info',
        'message': 'dbt lineage ' ~ loop.index ~ '/' ~ (parts | length) ~ ': ' ~ (part | length) ~ ' of ' ~ (edges | length) ~ ' edges',
        'attrs': attrs,
    }) -%}
  {%- endfor -%}
{% endmacro %}
