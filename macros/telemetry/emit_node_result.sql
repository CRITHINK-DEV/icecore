{#- UC1 end-of-run emitter `node_results` (var icecore.node_results, default true): one LOG per node,
    `icecore.kind = node_result`, with node_attributes(node, result) (dbt.result.status, rows_affected,
    execution_time_s, message, dbt.test.* for tests, snow.query.id) and, when the node has a span, its
    dbt.node.started_at / completed_at. Level error for error, fail and runtime error, warn for warn and partial
    success, info otherwise. Called by end_run as emit_node_results(results, batch). -#}
{% macro emit_node_results(results, batch) %}
  {%- for result in results -%}
    {%- do batch['items'].append(icecore._icecore_node_result_log(result, batch['node_spans'])) -%}
  {%- endfor -%}
{% endmacro %}


{% macro _icecore_node_result_log(result, node_spans={}) %}
  {%- set attrs = {'icecore.kind': 'node_result'} -%}
  {%- do attrs.update(icecore.node_attributes(none, result)) -%}
  {%- set span = node_spans.get(attrs.get('dbt.node.unique_id')) -%}
  {%- if span is not none -%}
    {%- for key in ['dbt.node.started_at', 'dbt.node.completed_at', 'dbt.node.duration_s'] -%}
      {%- do attrs.update({key: span['attrs'][key]}) -%}
    {%- endfor -%}
  {%- endif -%}
  {%- set status = (attrs.get('dbt.result.status') or 'unknown') | string | lower -%}
  {%- set level = 'error' if status in ['error', 'fail', 'runtime error']
      else ('warn' if status in ['warn', 'partial success'] else 'info') -%}
  {%- set detail = attrs.get('dbt.result.message') -%}
  {{ return({
      'type': 'log',
      'level': level,
      'message': (attrs.get('dbt.node.unique_id') or 'node') ~ ' ' ~ status ~ (': ' ~ detail if detail else ''),
      'attrs': attrs,
  }) }}
{% endmacro %}
