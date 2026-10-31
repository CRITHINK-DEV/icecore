{#-
  UC1 timing records: the spans end_run sends to ICECORE_EMIT_RUN, timed by dbt, never by ingestion
  (spikes/GATE1.md, "Consequences for the build"; OBSERVED_TIMESTAMP is NULL on icecore records, S3).

  _icecore_invocation_span(timing)  `icecore.invocation`: from run_started_at to the end_run hook.
  _icecore_node_span(result)        `icecore.node`: from the node's first timing start to its last timing end
                                    (result.timing: compile, then execute), with node_attributes(node).

  Times travel twice: as the span's own start and end (nanoseconds since the epoch, kept to the millisecond,
  S1) and as ISO-8601 UTC attributes, so they can be read whatever reads the record. Each CALL is its own
  trace and nesting across CALLs is unmeasured (GATE1.md, open item 6), so node spans are not parented to the
  invocation span; dbt.invocation_id groups them.
-#}

{#- The invocation's times, read once per end_run: run_started_at, and now (dbt's clock, UTC) as its end. -#}
{% macro _icecore_invocation_timing() %}
  {%- set started_ns = icecore._icecore_epoch_ns(run_started_at) -%}
  {%- set completed_ns = icecore._icecore_epoch_ns(modules.datetime.datetime.utcnow()) -%}
  {%- set started_ns = completed_ns if started_ns is none else started_ns -%}
  {{ return({
      'started_ns': started_ns,
      'completed_ns': completed_ns,
      'attrs': {
        'dbt.invocation.started_at': icecore._icecore_iso_of_ns(started_ns),
        'dbt.invocation.completed_at': icecore._icecore_iso_of_ns(completed_ns),
        'dbt.invocation.duration_s': (completed_ns - started_ns) / 1000000000.0,
      },
  }) }}
{% endmacro %}


{% macro _icecore_invocation_span(timing) %}
  {%- set attrs = {} -%}
  {%- do attrs.update(icecore._icecore_run_attributes()) -%}
  {%- do attrs.update(timing['attrs']) -%}
  {{ return({'name': 'icecore.invocation', 'start_ns': timing['started_ns'], 'end_ns': timing['completed_ns'],
             'attrs': attrs, 'events': []}) }}
{% endmacro %}


{#- One node span, or none when the result has no complete timing (a skipped node). Attributes: the node's keys,
    dbt.node.started_at / completed_at / duration_s, and dbt.timing.<phase>.started_at / completed_at as dbt
    wrote them (ISO strings are kept as they are; datetimes are written as ISO-8601 UTC). -#}
{% macro _icecore_node_span(result) %}
  {%- set get = icecore._icecore_get -%}
  {%- set attrs = icecore.node_attributes(get(result, 'node')) -%}
  {%- set span = namespace(start=none, end=none, started=none, completed=none) -%}
  {%- for phase in get(result, 'timing') or [] -%}
    {%- set name = get(phase, 'name') | string -%}
    {%- set started, completed = get(phase, 'started_at'), get(phase, 'completed_at') -%}
    {%- set started_ns, completed_ns = icecore._icecore_epoch_ns(started), icecore._icecore_epoch_ns(completed) -%}
    {%- if started_ns is not none -%}
      {%- do attrs.update({'dbt.timing.' ~ name ~ '.started_at': icecore._icecore_iso(started)}) -%}
      {%- if span.start is none or started_ns < span.start -%}
        {%- set span.start, span.started = started_ns, started -%}
      {%- endif -%}
    {%- endif -%}
    {%- if completed_ns is not none -%}
      {%- do attrs.update({'dbt.timing.' ~ name ~ '.completed_at': icecore._icecore_iso(completed)}) -%}
      {%- if span.end is none or completed_ns > span.end -%}
        {%- set span.end, span.completed = completed_ns, completed -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if span.start is none or span.end is none -%}
    {{ return(none) }}
  {%- endif -%}
  {%- do attrs.update({
      'dbt.node.started_at': icecore._icecore_iso(span.started),
      'dbt.node.completed_at': icecore._icecore_iso(span.completed),
      'dbt.node.duration_s': (span.end - span.start) / 1000000000.0,
  }) -%}
  {{ return({'name': 'icecore.node', 'start_ns': span.start, 'end_ns': span.end, 'attrs': attrs, 'events': []}) }}
{% endmacro %}


{#- Nanoseconds since the epoch of a datetime (naive is read as UTC, as dbt's timings are) or of an ISO-8601
    string (run_results.json: `Z`, an offset or no zone, up to 9 fractional digits). none when unreadable. -#}
{% macro _icecore_epoch_ns(value) %}
  {%- if value is none -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set datetime = modules.datetime -%}
  {%- set epoch = datetime.datetime(1970, 1, 1) -%}
  {%- if value is string -%}
    {%- set m = modules.re.match('^(\\d{4})-(\\d{2})-(\\d{2})[T ](\\d{2}):(\\d{2}):(\\d{2})(?:[.](\\d{1,9}))?(Z|[+-]\\d{2}:?\\d{2})?$', value | trim) -%}
    {%- if not m -%}
      {{ return(none) }}
    {%- endif -%}
    {%- set g = m.groups() -%}
    {%- set naive = datetime.datetime(g[0] | int, g[1] | int, g[2] | int, g[3] | int, g[4] | int, g[5] | int) -%}
    {%- set fraction = ((g[6] or '') ~ '000000000')[:9] | int -%}
    {%- set zone = g[7] or 'Z' -%}
    {%- set offset_s = 0 if zone == 'Z' else (1 if zone[0] == '+' else -1) * ((zone[1:3] | int) * 3600 + (zone[-2:] | int) * 60) -%}
    {%- set delta = naive - epoch -%}
    {{ return(((delta.days * 86400 + delta.seconds - offset_s) * 1000000000) + fraction) }}
  {%- endif -%}
  {%- if value.tzinfo is defined and value.tzinfo is not none -%}
    {%- set value = value.astimezone(modules.pytz.utc).replace(tzinfo=none) -%}
  {%- endif -%}
  {%- set delta = value - epoch -%}
  {{ return((delta.days * 86400 + delta.seconds) * 1000000000 + delta.microseconds * 1000) }}
{% endmacro %}


{#- ISO-8601 UTC with microseconds and `Z`, as run_results.json writes it; a string is kept as it is. -#}
{% macro _icecore_iso(value) %}
  {%- if value is none or value is string -%}
    {{ return(value) }}
  {%- endif -%}
  {{ return(icecore._icecore_iso_of_ns(icecore._icecore_epoch_ns(value))) }}
{% endmacro %}


{% macro _icecore_iso_of_ns(value) %}
  {%- set datetime = modules.datetime -%}
  {%- set moment = datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=(value // 1000)) -%}
  {{ return(moment.strftime('%Y-%m-%dT%H:%M:%S.%f') ~ 'Z') }}
{% endmacro %}
