{#-
  Monitor records sent at run end (spikes/GATE2.md, finding 6): an icecore monitor test no longer CALLs ICECORE_EMIT
  for its records. It writes them into its own compiled SQL as one comment, and end_run reads them back from dbt's
  `results` (each result's node.compiled_code, the field run_results.json documents) and sends them with the run's
  batch to ICECORE_EMIT_RUN, as span events stamped with the time the test made them. No state is shared between
  threads: dbt collects the results, and a record is taken only when its dbt.invocation_id is this run's.

  Sent at once, as before, unless all of: the node is a test, the hooks write (telemetry on, not `dbt compile`), an
  on-run-end hook of the project calls icecore.end_run(results), and the comment stays under 256 KB. So a project
  without the hook, or a test whose records would make its SQL too large, loses nothing.
  A run killed before on-run-end loses the records of its monitor tests (docs/configuration.md).
-#}


{#- What a monitor test renders before its SELECT: '' once the records are sent (icecore.emit_events), or the comment
    end_run reads back. events as emit_events takes them. -#}
{% macro _icecore_emit_from_test(events) %}
  {%- if not execute or not events -%}{{ return('') }}{%- endif -%}
  {%- if icecore._icecore_telemetry_enabled() and icecore._icecore_test_id() is not none and icecore._icecore_end_run_collects() -%}
    {%- set comment = icecore._icecore_records_comment(events) -%}
    {%- if comment | length <= 262144 -%}
      {{ return(comment) }}
    {%- endif -%}
  {%- endif -%}
  {%- do icecore.emit_events(events) -%}
  {{ return('') }}
{% endmacro %}


{#- The comment: the records as emit_events would send them (base attributes, payload cap), stamped now on Snowflake's
    clock when start_run measured how far dbt's clock is from it (else dbt's clock). '*' is written
    '\u002a' (the same JSON), so no value can open or end a comment inside it. -#}
{% macro _icecore_records_comment(events) %}
  {%- set run = ((graph.get('icecore') or {}).get('history') or {}).get(invocation_id) if graph is defined else none -%}
  {%- set text = tojson({'ts_ns': icecore._icecore_epoch_ns(modules.datetime.datetime.utcnow()) + ((run or {}).get('clock_offset_ns') or 0),
                         'records': icecore._icecore_emit_records(events)}) | replace('*', '\\u002a') -%}
  {{ return('/* icecore:records ' ~ text ~ ' */') }}
{% endmacro %}


{#- True when an on-run-end hook of the project calls icecore.end_run with dbt's results (hooks are operation nodes of
    dbt's graph). -#}
{% macro _icecore_end_run_collects() %}
  {%- if graph is not defined or graph.nodes is not defined -%}{{ return(false) }}{%- endif -%}
  {%- for node in graph.nodes.values() | selectattr('resource_type', 'equalto', 'operation') -%}
    {%- if '-on-run-end-' in node.unique_id and modules.re.search('icecore\\s*\\.\\s*end_run\\s*\\(\\s*(results\\s*=\\s*)?results\\s*\\)', node.raw_code or '') -%}
      {{ return(true) }}
    {%- endif -%}
  {%- endfor -%}
  {{ return(false) }}
{% endmacro %}


{#- end_run: the records the tests of `results` wrote, as span events at their own time, added to batch. On the
    test's node span, at most 100 (Snowflake keeps 128 events per span, S2); the rest, or all when the test has no node
    span, on spans `icecore.monitor` of at most 100. -#}
{% macro _icecore_attach_test_records(results, batch) %}
  {%- for result in results -%}
    {%- set node = icecore._icecore_get(result, 'node') -%}
    {%- set code = icecore._icecore_get(node, 'compiled_code') -%}
    {%- if code is string and '/* icecore:records ' in code -%}
      {%- set unique_id = icecore._icecore_get(node, 'unique_id') -%}
      {%- set events = [] -%}
      {%- for text in modules.re.findall('/\\* icecore:records (.*?) \\*/', code) -%}
        {%- set blob = fromjson(text) -%}
        {%- if blob is mapping and blob.get('ts_ns') is integer -%}
          {%- for r in blob.get('records') or [] if r is mapping and r.get('attrs') is mapping and r['attrs'].get('dbt.invocation_id') == invocation_id -%}
            {%- do events.append({'name': r.get('name'), 'ts_ns': blob['ts_ns'], 'attrs': r['attrs']}) -%}
          {%- endfor -%}
        {%- endif -%}
      {%- endfor -%}
      {%- set span = batch['node_spans'].get(unique_id) -%}
      {%- for start in range(0, events | length, 100) -%}
        {%- set chunk = events[start:start + 100] -%}
        {%- if loop.first and span is not none -%}
          {%- do span['events'].extend(chunk) -%}
        {%- else -%}
          {%- do batch['items'].append({'name': 'icecore.monitor', 'start_ns': chunk[0]['ts_ns'], 'end_ns': chunk[-1]['ts_ns'],
                                        'attrs': {'dbt.node.unique_id': unique_id}, 'events': chunk}) -%}
        {%- endif -%}
      {%- endfor -%}
    {%- endif -%}
  {%- endfor -%}
{% endmacro %}
