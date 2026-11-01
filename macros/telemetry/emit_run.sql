{#-
  UC1 run emitter: what end_run sends to ICECORE_EMIT_RUN (spikes/GATE1.md, option B).

  emit_run(items) takes the procedure's items as they are (procedures.sql):
    spans {name, start_ns, end_ns, attrs, events: [{name, ts_ns, attrs}]}, written with their explicit times;
    logs  {type: 'log', level, message, attrs}.
  Every record and every span event gets base_attributes() (so dbt.invocation_id is on each), then its own
  attributes, capped at payload_cap_kb like icecore.emit (a log's message counts towards its cap, as the key
  icecore.message). Records are sent in as many CALLs of at most 512 KB of JSON as needed (S2: no count
  limit applies to an ICECORE_EMIT_RUN CALL; 512 KB keeps dbt time low), each through the guard block.
  Each CALL is its own trace, so a run is grouped by dbt.invocation_id.
  Inputs are validated, never raised on: an item that is not a mapping, or a span without a numeric start_ns
  (the procedure would fail the whole CALL on it), is skipped. One warning per hook for failing CALLs (emit.sql).
-#}

{% macro emit_run(items) %}
  {%- set out = {'records': 0, 'calls': 0, 'statuses': []} -%}
  {%- if not execute or items is none or items is string or items is mapping or items is not iterable or not items -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set records = icecore._icecore_emit_run_records(items) -%}
  {%- set procedure = icecore._icecore_procedure('ICECORE_EMIT_RUN') -%}
  {%- for chunk in icecore._icecore_emit_run_chunks(records) -%}
    {%- do out['statuses'].append(icecore._icecore_guarded_status(procedure, chunk)) -%}
  {%- endfor -%}
  {%- do icecore._icecore_warn_failed_calls(procedure, out['statuses']) -%}
  {%- do out.update({'records': records | length, 'calls': out['statuses'] | length}) -%}
  {{ return(out) }}
{% endmacro %}


{#- Items -> records as ICECORE_EMIT_RUN takes them: base attributes added and capped, log levels lower case. -#}
{% macro _icecore_emit_run_records(items) %}
  {%- set base = icecore.base_attributes() -%}
  {%- set cap = icecore._icecore_payload_cap_bytes() -%}
  {%- set records = [] -%}
  {%- for item in items if item is mapping and (item.get('type') == 'log' or item.get('start_ns') is number) -%}
    {%- set record = {} -%}
    {%- do record.update(item) -%}
    {%- if item.get('type') == 'log' -%}
      {%- set attrs = icecore._icecore_run_attrs(base, item.get('attrs'), cap,
          '' if item.get('message') is none else item.get('message') | string) -%}
      {%- do record.update({
          'level': (item.get('level') or 'info') | string | lower,
          'message': attrs.pop('icecore.message'),
          'attrs': attrs,
      }) -%}
    {%- else -%}
      {%- set events = [] -%}
      {%- for event in item.get('events') or [] if event is mapping -%}
        {%- set copy = {} -%}
        {%- do copy.update(event) -%}
        {%- do copy.update({'attrs': icecore._icecore_run_attrs(base, event.get('attrs'), cap, none, true)}) -%}
        {%- do events.append(copy) -%}
      {%- endfor -%}
      {%- do record.update({'attrs': icecore._icecore_run_attrs(base, item.get('attrs'), cap, none, true), 'events': events}) -%}
    {%- endif -%}
    {%- do records.append(record) -%}
  {%- endfor -%}
  {{ return(records) }}
{% endmacro %}


{#- base + attrs, capped (as_json: mapping and list values as JSON text, for spans and span events, which drop them otherwise); with message, the message rides along as icecore.message so it is capped too. -#}
{% macro _icecore_run_attrs(base, attrs, cap, message=none, as_json=false) %}
  {%- set out = {} -%}
  {%- do out.update(base) -%}
  {%- do out.update(icecore._icecore_safe_attributes(attrs, as_json)) -%}
  {%- if message is not none -%}
    {%- do out.update({'icecore.message': message}) -%}
  {%- endif -%}
  {{ return(icecore._icecore_cap_attributes(out, cap)) }}
{% endmacro %}


{#- Records -> lists of at most max_bytes of JSON, by size only (the chunker of emit.sql, with no count limit). -#}
{% macro _icecore_emit_run_chunks(records, max_bytes=524288) %}
  {{ return(icecore._icecore_emit_chunks(records, (records | length) + 1, max_bytes)) }}
{% endmacro %}
