{#- Test wrappers for the UC1 timing helpers (macros/telemetry/spans.sql), for tests/test_timings.py. -#}

{% macro it_epoch_ns(values) %}
  {% set out = [] %}
  {% for value in values %}
    {% do out.append(icecore._icecore_epoch_ns(value)) %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}

{#- run_started_at (a tz-aware datetime) in ns and ISO, and the ISO of a naive datetime read as UTC. -#}
{% macro it_run_started_at() %}
  {% do _it_result({
      'ns': icecore._icecore_epoch_ns(run_started_at),
      'iso': icecore._icecore_iso(run_started_at),
      'iso_of_naive': icecore._icecore_iso(modules.datetime.datetime(2026, 10, 31, 17, 0, 0, 123456)),
  }) %}
{% endmacro %}

{#- The start record next to run_started_at, read in the same invocation. -#}
{% macro it_run_start_timing() %}
  {% do _it_result({
      'record': icecore._icecore_emit_records([icecore._icecore_run_start_event()])[0],
      'run_started_at': icecore._icecore_iso(run_started_at),
  }) %}
{% endmacro %}
