{#- UC1 end-of-run emitter `query_events` (var icecore.query_events, default true): one span event
    `icecore.query` per query id (collect_query_ids) on the node's `icecore.node` span, with `snow.query.id`
    and `dbt.node.unique_id`, timed at the span's end (the adapter response is the node's last statement). A
    node without a span (no timing) gets none. snow.query.id is the node's query, which feeds cost attribution;
    the record's RESOURCE_ATTRIBUTES snow.query.id is the emitting CALL's. Called by end_run as
    emit_query_events(results, batch). -#}
{% macro emit_query_events(results, batch) %}
  {%- for result in results -%}
    {%- set unique_id = icecore._icecore_get(result, 'unique_id') or icecore._icecore_get(icecore._icecore_get(result, 'node'), 'unique_id') -%}
    {%- set span = batch['node_spans'].get(unique_id) -%}
    {%- if span is not none -%}
      {%- for query_id in icecore.collect_query_ids(result) -%}
        {%- do span['events'].append({'name': 'icecore.query', 'ts_ns': span['end_ns'],
            'attrs': {'snow.query.id': query_id, 'dbt.node.unique_id': unique_id}}) -%}
      {%- endfor -%}
    {%- endif -%}
  {%- endfor -%}
{% endmacro %}
