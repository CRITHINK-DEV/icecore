{#-
  UC1 writer: the one way icecore writes a record during a run (spikes/GATE1.md, option B).

  emit(kind, message, attributes, level)  one record.
  emit_events(events)                     many records, each {kind, message, attributes, level}.

  Each record becomes one SPAN_EVENT named `icecore.<kind>` (SQL cannot set SCOPE, so the name prefix is the
  package identity), carrying base_attributes() (dbt.invocation_id on every record), the caller's attributes,
  `icecore.level` and `icecore.message`. Records are capped at payload_cap_kb (default 64, clamped to 1..256),
  cutting the longest strings and flagging the record. They are sent in chunks of at most 100 events and
  512 KB per CALL of <target db>.<obs schema>.ICECORE_EMIT, each CALL inside the guard block: a missing
  procedure or grant comes back as `icecore-error <code>: <message>` and is logged as a warning.

  Never INSERT, never emit from an anonymous block (the guard only wraps the CALL), never change session levels.
-#}

{% macro emit(kind, message=none, attributes=none, level='info') %}
  {{ return(icecore.emit_events([{'kind': kind, 'message': message, 'attributes': attributes, 'level': level}])) }}
{% endmacro %}


{% macro emit_events(events) %}
  {%- set out = {'events': 0, 'calls': 0, 'statuses': []} -%}
  {%- if not execute or not events -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set records = icecore._icecore_emit_records(events) -%}
  {%- set procedure = icecore._icecore_procedure('ICECORE_EMIT') -%}
  {%- for chunk in icecore._icecore_emit_chunks(records) -%}
    {%- do out['statuses'].append(icecore._icecore_guarded_call(procedure, chunk)) -%}
  {%- endfor -%}
  {%- do out.update({'events': records | length, 'calls': out['statuses'] | length}) -%}
  {{ return(out) }}
{% endmacro %}


{#- Events -> [{name, attrs}] as ICECORE_EMIT takes them, capped. -#}
{% macro _icecore_emit_records(events) %}
  {%- set base = icecore.base_attributes() -%}
  {%- set cap = icecore._icecore_payload_cap_bytes() -%}
  {%- set records = [] -%}
  {%- for event in events -%}
    {%- set kind = (event.get('kind') or 'event') | string -%}
    {%- set attrs = {} -%}
    {%- do attrs.update(base) -%}
    {%- do attrs.update(event.get('attributes') or {}) -%}
    {%- do attrs.update({'icecore.level': (event.get('level') or 'info') | string | lower}) -%}
    {%- if event.get('message') is not none -%}
      {%- do attrs.update({'icecore.message': event.get('message') | string}) -%}
    {%- endif -%}
    {%- do records.append({
        'name': kind if kind.startswith('icecore.') else 'icecore.' ~ kind,
        'attrs': icecore._icecore_cap_attributes(attrs, cap),
    }) -%}
  {%- endfor -%}
  {{ return(records) }}
{% endmacro %}


{#- payload_cap_kb in bytes: default 64, clamped to 1..256 (S2: Snowflake silently drops a record at about 1 MB). -#}
{% macro _icecore_payload_cap_bytes() %}
  {%- set kb = icecore._icecore_number(var('icecore', {}).get('payload_cap_kb', 64)) -%}
  {%- set kb = 64 if kb is none or kb is boolean else kb | int -%}
  {{ return(([[kb, 1] | max, 256] | min) * 1024) }}
{% endmacro %}


{#- Attributes whose JSON (as sent) exceeds cap bytes: cut the longest string values until it fits, and flag
    the record with icecore.truncated = true and icecore.truncated_keys = 'key1,key2'. Sizes are measured on
    the ASCII JSON that is sent (non-ASCII escaped), which never under-counts the stored size. -#}
{% macro _icecore_cap_attributes(attrs, cap) %}
  {%- if (tojson(attrs) | length) <= cap -%}
    {{ return(attrs) }}
  {%- endif -%}
  {%- set out = {} -%}
  {%- do out.update(attrs) -%}
  {%- set cut = [] -%}
  {%- set ns = namespace(done=false) -%}
  {%- for _ in range((attrs | length) + 8) -%}
    {%- if not ns.done -%}
      {%- do out.update({'icecore.truncated': true, 'icecore.truncated_keys': cut | join(',')}) -%}
      {%- set longest = namespace(key=none, size=0) -%}
      {%- for key, value in out.items() -%}
        {%- if value is string and key != 'icecore.truncated_keys' and (tojson(value) | length) > longest.size -%}
          {%- set longest.key = key -%}
          {%- set longest.size = tojson(value) | length -%}
        {%- endif -%}
      {%- endfor -%}
      {%- if (tojson(out) | length) <= cap or longest.key is none or longest.size <= 2 -%}
        {%- set ns.done = true -%}
      {%- else -%}
        {%- if longest.key not in cut -%}
          {%- do cut.append(longest.key) -%}
          {%- do out.update({'icecore.truncated_keys': cut | join(',')}) -%}
        {%- endif -%}
        {%- set over = (tojson(out) | length) - cap + 16 -%}
        {%- set raw = out[longest.key] -%}
        {%- set keep_size = longest.size - 2 - over -%}
        {%- set keep = ((raw | length) * keep_size / (longest.size - 2)) | int if keep_size > 0 else 0 -%}
        {%- do out.update({longest.key: raw[:keep]}) -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- Records -> lists of at most max_events records and max_bytes of JSON (S2: a CALL keeps only its last 128
    events, and lands none at about 1 MB). -#}
{% macro _icecore_emit_chunks(records, max_events=100, max_bytes=524288) %}
  {%- set chunks = [] -%}
  {%- set ns = namespace(current=[], size=2) -%}
  {%- for record in records -%}
    {%- set size = (tojson(record) | length) + 2 -%}
    {%- if ns.current and ((ns.current | length) >= max_events or ns.size + size > max_bytes) -%}
      {%- do chunks.append(ns.current) -%}
      {%- set ns.current = [] -%}
      {%- set ns.size = 2 -%}
    {%- endif -%}
    {%- do ns.current.append(record) -%}
    {%- set ns.size = ns.size + size -%}
  {%- endfor -%}
  {%- if ns.current -%}
    {%- do chunks.append(ns.current) -%}
  {%- endif -%}
  {{ return(chunks) }}
{% endmacro %}


{#- The guard block S1 measured: an error inside the CALL comes back as a status, never as a failure. -#}
{% macro _icecore_guarded_call_sql(procedure, items) %}
  {%- set sql -%}
EXECUTE IMMEDIATE $$
DECLARE
  r STRING;
BEGIN
  CALL {{ procedure }}(PARSE_JSON({{ icecore._icecore_block_literal(tojson(items)) }})) INTO :r;
  RETURN r;
EXCEPTION
  WHEN OTHER THEN RETURN 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
END;
$$
  {%- endset -%}
  {{ return(sql | trim) }}
{% endmacro %}


{#- One guarded CALL of procedure with items (an array). Returns the procedure's status; an `icecore-error`
    status is logged as a warning (not exceptions.warn, which --warn-error would turn into a failure). -#}
{% macro _icecore_guarded_call(procedure, items) %}
  {%- if not execute -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set status = run_query(icecore._icecore_guarded_call_sql(procedure, items)).columns[0].values()[0] | string -%}
  {%- if status.startswith('icecore-error') -%}
    {%- do log('icecore: warning: telemetry not written (' ~ procedure ~ '): ' ~ status, info=True) -%}
  {%- endif -%}
  {{ return(status) }}
{% endmacro %}


{#- Single-quoted literal safe inside a $$ block: backslash first, then quote; `$` becomes \x24 so the value can
    never close the block; CR and LF are escaped so the statement stays on one line. -#}
{% macro _icecore_block_literal(value) %}
  {%- set s = (value | string).replace('\\', '\\\\').replace("'", "\\'").replace('$', '\\x24') -%}
  {{ return("'" ~ s.replace('\n', '\\n').replace('\r', '\\r') ~ "'") }}
{% endmacro %}
