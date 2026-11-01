{#-
  UC1 on-run-end hook: `on-run-end: "{{ icecore.end_run(results) }}"`.

  One orchestrator. It builds one batch for the run and sends it with icecore.emit_run to ICECORE_EMIT_RUN, in
  as many guarded CALLs of at most 512 KB as needed (spikes/GATE1.md, "Consequences for the build"). In order:
    1. the invocation span and one span per node, with dbt's own timings (spans.sql);
    2. each emitter of _icecore_end_run_emitters() whose var is on adds its items to the batch;
    3. the invocation-end LOG: dbt.invocation.status (success, warn or error), the node counts and the
       invocation's times.
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
    items to batch['items'] (the ICECORE_EMIT_RUN shapes of emit_run.sql), or events to a node's span in
    batch['node_spans'][unique_id]['events']. batch['timing'] holds the invocation's times. Later stories add
    theirs here: lineage, samples, cost, root cause. -#}
{% macro _icecore_end_run_emitters() %}
  {{ return([
      {'name': 'node_results', 'var': 'node_results', 'default': true, 'macro': icecore.emit_node_results},
      {'name': 'query_events', 'var': 'query_events', 'default': true, 'macro': icecore.emit_query_events},
      {'name': 'lineage', 'var': 'lineage', 'default': true, 'macro': icecore.emit_lineage},
      {'name': 'samples', 'var': 'samples', 'default': false, 'macro': icecore.emit_samples},
      {'name': 'root_cause', 'var': 'root_cause', 'default': false, 'macro': icecore.emit_root_cause},
      {'name': 'cost', 'var': 'cost', 'default': false, 'macro': icecore.attach_cost},
  ]) }}
{% endmacro %}


{#- The batch end_run sends for these results: the spans, the emitters' items, then the invocation-end LOG.
    Isolation of the emitters: an emitter runs only when its var is on and its entry is usable (a mapping with a
    callable macro); one that returns `{'error': <text>}` (a failed guarded query) or has a broken entry is
    skipped, and the loop goes on with the next. One warning per hook sums them up. A Jinja exception inside an
    emitter cannot be caught and would still stop the hook: emitters must only build data and validate their
    inputs. extra_emitters are run after the package's own (tests; same entry shape). -#}
{% macro _icecore_end_run_items(results, extra_emitters=none) %}
  {%- set results = results if (results is iterable and results is not string and results is not mapping) else [] -%}
  {%- set results = icecore._icecore_node_results(results) -%}
  {%- set timing = icecore._icecore_invocation_timing() -%}
  {%- set batch = {'items': [icecore._icecore_invocation_span(timing)], 'node_spans': {}, 'timing': timing} -%}
  {%- for result in results -%}
    {%- set span = icecore._icecore_node_span(result) -%}
    {%- if span is not none -%}
      {%- do batch['items'].append(span) -%}
      {%- do batch['node_spans'].update({span['attrs'].get('dbt.node.unique_id'): span}) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set skipped = [] -%}
  {%- for emitter in icecore._icecore_end_run_emitters() + (extra_emitters or []) -%}
    {%- if emitter is not mapping or emitter.get('macro') is not callable -%}
      {%- do skipped.append((emitter.get('name') if emitter is mapping else 'entry') ~ ': not a usable emitter entry') -%}
    {%- elif icecore._icecore_flag(emitter.get('var'), emitter.get('default')) -%}
      {%- set outcome = emitter['macro'](results, batch) -%}
      {%- if outcome is mapping and outcome.get('error') -%}
        {%- do skipped.append((emitter.get('name') or 'emitter') ~ ': ' ~ outcome.get('error')) -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if skipped -%}
    {%- do log('icecore: warning: end_run emitters reported errors, the others still ran (' ~ (skipped | join('; ')) ~ ')', info=True) -%}
  {%- endif -%}
  {%- do batch['items'].append(icecore._icecore_run_end_log(results, timing)) -%}
  {{ return(batch['items']) }}
{% endmacro %}


{#- The invocation-end LOG: status, node counts and the invocation's times, at level error, warn or info to match the status. -#}
{% macro _icecore_run_end_log(results, timing=none) %}
  {%- set counts = icecore._icecore_node_counts(results) -%}
  {%- set status = 'error' if counts['failed'] else ('warn' if counts['warned'] else 'success') -%}
  {%- set attrs = {'icecore.kind': 'run_end', 'dbt.invocation.status': status} -%}
  {%- do attrs.update(icecore._icecore_run_attributes()) -%}
  {%- do attrs.update(timing['attrs'] if timing is not none else {}) -%}
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


{#- The results of nodes: hook operations (unique_id operation.*) are left out, whatever the dbt version passes, and
    so is anything that is not a result (none, text, a number). -#}
{% macro _icecore_node_results(results) %}
  {%- set out = [] -%}
  {%- for result in results or [] if result is not none and result is not string and result is not number -%}
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


{#- The rows end_run writes for `nodes` nodes, read by the cost estimate (icecore.estimate_telemetry_cost), kept
    next to _icecore_end_run_emitters() so the two change together.
      fixed     rows every run writes: invocation_span (1), node_spans (one per node), end (the invocation-end LOG);
      emitters  rows of each emitter of the list above, 0 when its var is off (read as end_run reads it);
      uncounted names of emitters in the list that have no count here.
    An emitter added to the list needs its count here: a test fails until it has one.
    lineage    the records of the graph's edges split under the payload cap (0 with no edges);
    samples    one per failing test with store_failures, failing_tests of them (the caller knows; the graph does
               not), 0 when var samples is off (the default). Warn-severity failures count only if the caller counts them.
    root_cause is 0 while node_results is on (it annotates the failed tests' node_result LOGs); with node_results off it
               writes one LOG per explained test, at most one per node, so `nodes` is the upper bound used. -#}
{% macro _icecore_end_run_row_counts(nodes, query_events_per_node=1, failing_tests=0) %}
  {%- set nodes = nodes | int -%}
  {%- set per_emitter = {
      'node_results': nodes,
      'query_events': nodes * (query_events_per_node | int),
      'root_cause': 0 if icecore._icecore_flag('node_results', true) else nodes,
  } -%}
  {%- set out = {'fixed': {'invocation_span': 1, 'node_spans': nodes, 'end': 1}, 'emitters': {}, 'uncounted': []} -%}
  {%- for emitter in icecore._icecore_end_run_emitters() -%}
    {%- set name = emitter['name'] -%}
    {%- if name not in per_emitter and name not in ['lineage', 'samples'] -%}
      {%- do out['uncounted'].append(name) -%}
    {%- elif not icecore._icecore_flag(emitter['var'], emitter['default']) -%}
      {%- do out['emitters'].update({name: 0}) -%}
    {%- elif name in per_emitter -%}
      {%- do out['emitters'].update({name: per_emitter[name]}) -%}
    {%- elif name == 'lineage' -%}
      {%- do out['emitters'].update({name: icecore._icecore_lineage_parts(icecore._icecore_lineage_edges(graph)) | length}) -%}
    {%- else -%}
      {%- do out['emitters'].update({name: failing_tests | int}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}
