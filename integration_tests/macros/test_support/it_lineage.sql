{#- Test wrappers for the lineage emitter (tests/test_lineage.py). Results are logged as `icecore_it_result:<json>`. -#}

{#- The edges of this project's graph, as emit_lineage reads them. -#}
{% macro it_lineage_edges() %}
  {% do _it_result(icecore._icecore_lineage_edges(graph)) %}
{% endmacro %}

{#- The LOG items for these edges, as ICECORE_EMIT_RUN receives them (base attributes added, capped), and their sizes. -#}
{% macro it_lineage_render(edges, info=none) %}
  {% set batch = {'items': [], 'node_spans': {}} %}
  {% do icecore._icecore_lineage_append(edges, batch, info) %}
  {% set records = icecore._icecore_emit_run_records(batch['items']) %}
  {% set sizes = [] %}
  {% for record in records %}
    {% do sizes.append((tojson(record['attrs']) | length) + (record['message'] | length)) %}
  {% endfor %}
  {% do _it_result({'records': records, 'sizes': sizes}) %}
{% endmacro %}
