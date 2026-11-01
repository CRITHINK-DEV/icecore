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

  Failure isolation. dbt's Jinja has no try/except: a Jinja exception inside a macro cannot be caught, so isolation
  is built from what can be. (a) Every query an emitter makes runs inside a Snowflake Scripting guard
  (`exception when other`) and comes back as a status. (b) Emitters only build data and validate their inputs
  (none, wrong types: bad events are skipped, bad attributes coerced), so they do not raise. (c) end_run's loop
  carries on after an emitter that returns an error. The run_query of the guard block itself can still raise (no
  warehouse, a closed connection) and Jinja cannot catch it. Chunks are measured on the escaped
  literal so a statement stays near 512 KB (one record alone is at most four times its 256 KB cap).
  A failing CALL logs one warning per hook (the statuses of all its CALLs summed up), not one per CALL.
-#}

{% macro emit(kind, message=none, attributes=none, level='info') %}
  {{ return(icecore.emit_events([{'kind': kind, 'message': message, 'attributes': attributes, 'level': level}])) }}
{% endmacro %}


{% macro emit_events(events) %}
  {%- set out = {'events': 0, 'calls': 0, 'statuses': []} -%}
  {%- set events = [events] if events is mapping else events -%}
  {%- if not execute or events is none or events is string or events is not iterable -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set records = icecore._icecore_emit_records(events) -%}
  {%- if not records -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set target_procedure = icecore._icecore_procedure_or_error('ICECORE_EMIT') -%}
  {%- if target_procedure['error'] -%}
    {%- do icecore._icecore_warn_no_procedure(target_procedure['error']) -%}
    {{ return(out) }}
  {%- endif -%}
  {%- set procedure = target_procedure['procedure'] -%}
  {%- for chunk in icecore._icecore_emit_chunks(records) -%}
    {%- do out['statuses'].append(icecore._icecore_guarded_status(procedure, chunk)) -%}
  {%- endfor -%}
  {%- do icecore._icecore_warn_failed_calls(procedure, out['statuses']) -%}
  {%- do out.update({'events': records | length, 'calls': out['statuses'] | length}) -%}
  {{ return(out) }}
{% endmacro %}


{#- Events -> [{name, attrs}] as ICECORE_EMIT takes them, capped. -#}
{% macro _icecore_emit_records(events) %}
  {%- set base = icecore.base_attributes() -%}
  {%- set cap = icecore._icecore_payload_cap_bytes() -%}
  {%- set records = [] -%}
  {%- for event in events if event is mapping -%}
    {%- set kind = (event.get('kind') or 'event') | string -%}
    {%- set attrs = {} -%}
    {%- do attrs.update(base) -%}
    {%- do attrs.update(icecore._icecore_safe_attributes(event.get('attributes'), true)) -%}
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


{#- Attributes as a dict that tojson can always serialise: not a mapping reads as none, keys become strings, and
    every value is cleaned by _icecore_safe_value, nested ones too. A bad value never raises. With as_json, a mapping
    or list value is then written as a JSON string (tojson): SYSTEM$ADD_EVENT, behind ICECORE_EMIT, silently drops
    object and list values, so emit keeps them as text. ICECORE_EMIT_RUN LOG records keep native nesting. -#}
{% macro _icecore_safe_attributes(attrs, as_json=false) %}
  {%- set out = {} -%}
  {%- if attrs is mapping -%}
    {%- for key, value in attrs.items() -%}
      {%- set clean = icecore._icecore_safe_value(value) -%}
      {%- do out.update({key | string: tojson(clean) if as_json and (clean is mapping or (clean is sequence and clean is not string)) else clean}) -%}
    {%- endfor -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- One value as JSON can carry it. none, strings and booleans stay. A number is re-read from its text, so a Decimal
    (what run_query returns) becomes an int or a float, and NaN or infinity becomes none. A date or datetime becomes
    its ISO text. A mapping and a list are cleaned element by element, recursively. Anything else becomes its text. -#}
{% macro _icecore_safe_value(value) %}
  {%- if value is none or value is string or value is boolean -%}
    {{ return(value) }}
  {%- elif value is number -%}
    {{ return(icecore._icecore_number(value | string)) }}
  {%- elif value is mapping -%}
    {%- set out = {} -%}
    {%- for key, item in value.items() -%}
      {%- do out.update({key | string: icecore._icecore_safe_value(item)}) -%}
    {%- endfor -%}
    {{ return(out) }}
  {%- elif value is sequence -%}
    {%- set out = [] -%}
    {%- for item in value -%}
      {%- do out.append(icecore._icecore_safe_value(item)) -%}
    {%- endfor -%}
    {{ return(out) }}
  {%- elif value.isoformat is defined -%}
    {{ return(value.isoformat()) }}
  {%- endif -%}
  {{ return(value | string) }}
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


{#- Records -> lists of at most max_events records and max_bytes of the literal as it is sent: the escaped JSON
    (backslash and quote doubled, `$` as four characters), not the raw JSON, which under-counts it up to four
    times. A record larger than max_bytes alone goes in a chunk of its own and is sent.
    (S2: a CALL keeps only its last 128 events, and lands none at about 1 MB.) -#}
{% macro _icecore_emit_chunks(records, max_events=100, max_bytes=524288) %}
  {%- set chunks = [] -%}
  {%- set ns = namespace(current=[], size=4) -%}
  {%- for record in records -%}
    {%- set size = (icecore._sql(tojson(record), 'string') | length) - 1 -%}
    {%- if ns.current and ((ns.current | length) >= max_events or ns.size + size > max_bytes) -%}
      {%- do chunks.append(ns.current) -%}
      {%- set ns.current = [] -%}
      {%- set ns.size = 4 -%}
    {%- endif -%}
    {%- do ns.current.append(record) -%}
    {%- set ns.size = ns.size + size -%}
  {%- endfor -%}
  {%- if ns.current -%}
    {%- do chunks.append(ns.current) -%}
  {%- endif -%}
  {{ return(chunks) }}
{% endmacro %}


{#- Why `procedure` cannot go into a guard block, or none when it can. A procedure name is only ever built by
    icecore._icecore_procedure, from parts that went through icecore._sql: dotted, each part double-quoted with
    embedded quotes doubled. Any such name is accepted (a database like MY-DB or "My DB" is valid); what is checked
    is the structure, so a quote can never end a part early, plus the two things that matter inside a `$$` body:
    no dollar sign and no control character. -#}
{% macro _icecore_procedure_problem(procedure) %}
  {%- if procedure is not string or not modules.re.match('^"(?:[^"\\x00-\\x1f\\x7f$]|"")+"(?:\\."(?:[^"\\x00-\\x1f\\x7f$]|"")+")*\\Z', procedure) -%}
    {{ return('icecore: invalid procedure name ' ~ ((procedure | string)[:60] | tojson) ~ ': build it with icecore._icecore_procedure') }}
  {%- endif -%}
  {{ return(none) }}
{% endmacro %}


{#- The hook path's way to a procedure name: {procedure, error}. error is set (and procedure none) when the target
    database, the obs schema or the name would not pass icecore._sql, so a hook can warn and skip instead of raising. -#}
{% macro _icecore_procedure_or_error(name, schema=none) %}
  {%- set database = target.database | string | upper -%}
  {%- set obs = (schema or var('icecore', {}).get('obs_schema', 'ICECORE_OBS')) | string | upper -%}
  {%- set error = icecore._sql_error(database, 'name') or icecore._sql_error(obs, 'ident') or icecore._sql_error(name, 'ident') -%}
  {%- if error -%}
    {{ return({'procedure': none, 'error': error}) }}
  {%- endif -%}
  {{ return({'procedure': icecore._icecore_procedure(name, schema), 'error': none}) }}
{% endmacro %}


{#- Hook path: no procedure name means a warning and nothing sent; never an exception. -#}
{% macro _icecore_warn_no_procedure(error) %}
  {%- do log('icecore: warning: telemetry not written: ' ~ error, info=True) -%}
{% endmacro %}


{#- The guard block S1 measured: an error inside the CALL comes back as a status, never as a failure. -#}
{% macro _icecore_guarded_call_sql(procedure, items) %}
  {%- set problem = icecore._icecore_procedure_problem(procedure) -%}
  {%- if problem is not none -%}
    {{ exceptions.raise_compiler_error(problem) }}
  {%- endif -%}
  {%- set sql -%}
EXECUTE IMMEDIATE $$
DECLARE
  r STRING;
BEGIN
  CALL {{ procedure }}(PARSE_JSON({{ icecore._sql(tojson(items), 'string') }})) INTO :r;
  RETURN r;
EXCEPTION
  WHEN OTHER THEN RETURN 'icecore-error ' || SQLCODE || ': ' || SQLERRM;
END;
$$
  {%- endset -%}
  {{ return(sql | trim) }}
{% endmacro %}


{#- One guarded CALL of procedure with items (an array); returns the procedure's status, or `icecore-error ...`.
    It logs nothing: callers log with _icecore_warn_failed_calls, once per hook. Bad input and an empty
    result come back as a status. The run_query of the guard block can still raise (no warehouse, a closed
    connection): Jinja cannot catch that. -#}
{% macro _icecore_guarded_status(procedure, items) %}
  {%- if not execute -%}
    {{ return(none) }}
  {%- endif -%}
  {%- if icecore._icecore_procedure_problem(procedure) is not none -%}
    {{ return('icecore-error invalid_procedure: ' ~ icecore._icecore_procedure_problem(procedure)) }}
  {%- endif -%}
  {%- set items = items if (items is iterable and items is not string and items is not mapping) else [] -%}
  {%- set rows = run_query(icecore._icecore_guarded_call_sql(procedure, items)).rows -%}
  {%- if not rows or not rows[0] or rows[0][0] is none -%}
    {{ return('icecore-error no_status: the guard returned nothing') }}
  {%- endif -%}
  {{ return(rows[0][0] | string) }}
{% endmacro %}


{#- One warning for a hook's CALLs: the first failing status, and how many of the CALLs failed when several did.
    Not exceptions.warn, which --warn-error would turn into a failure. -#}
{% macro _icecore_warn_failed_calls(procedure, statuses) %}
  {%- set failed = [] -%}
  {%- for status in statuses if status is string and status.startswith('icecore-error') -%}
    {%- do failed.append(status) -%}
  {%- endfor -%}
  {%- if failed -%}
    {%- do log('icecore: warning: telemetry not written (' ~ procedure ~ '): ' ~ failed[0]
        ~ (' (' ~ (failed | length) ~ ' of ' ~ (statuses | length) ~ ' CALLs failed)' if statuses | length > 1 else ''), info=True) -%}
  {%- endif -%}
{% endmacro %}


{#- One guarded CALL with its own warning (setup, doctor): _icecore_guarded_status, then _icecore_warn_failed_calls. -#}
{% macro _icecore_guarded_call(procedure, items) %}
  {%- set status = icecore._icecore_guarded_status(procedure, items) -%}
  {%- do icecore._icecore_warn_failed_calls(procedure, [status]) -%}
  {{ return(status) }}
{% endmacro %}


{#- Wrapper kept for one release: icecore._sql(value, 'string'). -#}
{% macro _icecore_block_literal(value) %}
  {{ return(icecore._sql(value, 'string')) }}
{% endmacro %}
