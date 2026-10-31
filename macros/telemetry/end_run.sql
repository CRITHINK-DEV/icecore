{#-
  UC1 on-run-end hook: `on-run-end: "{{ icecore.end_run(results) }}"`.

  One orchestrator. It builds one batch for the run and sends it with icecore.emit_run to ICECORE_EMIT_RUN, in
  as many guarded CALLs of at most 512 KB as needed (spikes/GATE1.md, "Consequences for the build"). In order:
    1. each emitter of _icecore_end_run_emitters() whose var is on adds its items to the batch;
    2. the invocation-end LOG: dbt.invocation.status (success, warn or error) and the node counts.
  Renders '' so the hook itself runs no SQL. A no-op at parse time and when var('icecore').telemetry is false.
-#}

{% macro end_run(results=none) %}
  {%- if execute and icecore._icecore_telemetry_enabled() -%}
    {%- do icecore.emit_run(icecore._icecore_end_run_items(results)) -%}
  {%- endif -%}
  {{ return('') }}
{% endmacro %}


{#- The extension point: one entry per end-of-run emitter, {name, var, default, macro}. The emitter runs when
    var('icecore')[var] is true (default `default`); it is called as macro(results, batch) and appends its
    items to batch['items'] (the ICECORE_EMIT_RUN shapes of emit_run.sql). Later stories add theirs here:
    lineage, samples, cost, root cause. -#}
{% macro _icecore_end_run_emitters() %}
  {{ return([]) }}
{% endmacro %}


{#- The batch end_run sends for these results: the emitters' items, then the invocation-end LOG. -#}
{% macro _icecore_end_run_items(results) %}
  {%- set results = results or [] -%}
  {%- set batch = {'items': []} -%}
  {%- for emitter in icecore._icecore_end_run_emitters() -%}
    {%- if icecore._icecore_flag(emitter['var'], emitter['default']) -%}
      {%- do emitter['macro'](results, batch) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- do batch['items'].append(icecore._icecore_run_end_log(results)) -%}
  {{ return(batch['items']) }}
{% endmacro %}


{#- The invocation-end LOG: status and node counts, at level error, warn or info to match the status. -#}
{% macro _icecore_run_end_log(results) %}
  {%- set counts = icecore._icecore_node_counts(results) -%}
  {%- set status = 'error' if counts['failed'] else ('warn' if counts['warned'] else 'success') -%}
  {%- set attrs = {'icecore.kind': 'run_end', 'dbt.invocation.status': status} -%}
  {%- do attrs.update(icecore._icecore_run_attributes()) -%}
  {%- for key in ['total', 'success', 'failed', 'warned', 'skipped'] -%}
    {%- do attrs.update({'dbt.invocation.nodes_' ~ key: counts[key]}) -%}
  {%- endfor -%}
  {{ return({
      'type': 'log',
      'level': {'error': 'error', 'warn': 'warn'}.get(status, 'info'),
      'message': 'dbt ' ~ flags.WHICH ~ ' finished: ' ~ status ~ ', ' ~ counts['total'] ~ ' nodes ('
          ~ counts['success'] ~ ' succeeded, ' ~ counts['failed'] ~ ' failed, ' ~ counts['warned'] ~ ' warned, '
          ~ counts['skipped'] ~ ' skipped)',
      'attrs': attrs,
  }) }}
{% endmacro %}


{#- The results of nodes: hook operations (unique_id operation.*) are left out, whatever the dbt version passes. -#}
{% macro _icecore_node_results(results) %}
  {%- set out = [] -%}
  {%- for result in results or [] -%}
    {%- set unique_id = icecore._icecore_get(result, 'unique_id') or icecore._icecore_get(icecore._icecore_get(result, 'node'), 'unique_id') -%}
    {%- if not (unique_id | string).startswith('operation.') -%}
      {%- do out.append(result) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- Nodes per outcome. failed: error, fail, runtime error. warned: warn, partial success. skipped: skipped.
    success: everything else (success, pass, no-op). -#}
{% macro _icecore_node_counts(results) %}
  {%- set counts = {'total': 0, 'success': 0, 'failed': 0, 'warned': 0, 'skipped': 0} -%}
  {%- for result in icecore._icecore_node_results(results) -%}
    {%- set status = icecore._icecore_get(result, 'status') | string | lower -%}
    {%- set outcome = 'failed' if status in ['error', 'fail', 'runtime error']
        else ('warned' if status in ['warn', 'partial success']
        else ('skipped' if status == 'skipped' else 'success')) -%}
    {%- do counts.update({'total': counts['total'] + 1, outcome: counts[outcome] + 1}) -%}
  {%- endfor -%}
  {{ return(counts) }}
{% endmacro %}
