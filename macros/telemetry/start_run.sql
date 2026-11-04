{#-
  UC1 on-run-start hook: `on-run-start: "{{ icecore.start_run() }}"`.

  Writes one record, the span event `icecore.run_start`, through icecore.emit (ICECORE_EMIT, guarded), with
  base_attributes() plus dbt.version, icecore.anomaly_engine and icecore.shadow_engine, so every run records
  what it ran with, and icecore.layer_standard when var icecore.layer_standard is a list (read by INVOCATIONS and
  the app's layer design checks). Renders '' so the hook itself runs no SQL. A no-op at parse time and when
  var('icecore').telemetry is false.
-#}

{% macro start_run() %}
  {%- if execute and icecore._icecore_telemetry_enabled() -%}
    {%- do icecore.emit_events([icecore._icecore_run_start_event()]) -%}
  {%- endif -%}
  {{ return('') }}
{% endmacro %}


{#- The start record as emit_events takes it: {kind, message, attributes}. -#}
{% macro _icecore_run_start_event() %}
  {{ return({
      'kind': 'run_start',
      'message': 'dbt ' ~ flags.WHICH ~ ' started',
      'attributes': icecore._icecore_run_start_attributes(),
  }) }}
{% endmacro %}


{#- The run settings plus dbt's own start time (an ICECORE_EMIT span event is stamped with the CALL's time, so
    dbt timings travel as attributes), and the declared layer standard. -#}
{% macro _icecore_run_start_attributes() %}
  {%- set attrs = icecore._icecore_run_attributes() -%}
  {%- do attrs.update({'dbt.invocation.started_at': icecore._icecore_iso(run_started_at)}) -%}
  {%- set standard = icecore._icecore_layer_standard(icecore._icecore_setting('layer_standard')) -%}
  {%- if standard is not none -%}
    {%- do attrs.update({'icecore.layer_standard': standard}) -%}
  {%- endif -%}
  {{ return(attrs) }}
{% endmacro %}


{#- var icecore.layer_standard as a list of layer names (each trimmed, empty ones dropped), or none when it is not a
    list. It is data: emit sends it as JSON text inside the CALL's one literal (icecore._sql), never as SQL. -#}
{% macro _icecore_layer_standard(value) %}
  {%- if value is none or value is string or value is mapping or value is not iterable -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set out = [] -%}
  {%- for item in value if item is not none and (item | string | trim) != '' -%}
    {%- do out.append(item | string | trim) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- What the run ran with, on the start record and the invocation-end record (spec section 4). -#}
{% macro _icecore_run_attributes() %}
  {{ return({
      'dbt.version': dbt_version | string,
      'icecore.anomaly_engine': icecore._icecore_setting('anomaly_engine', 'auto') | string,
      'icecore.shadow_engine': icecore._icecore_setting('shadow_engine', 'none') | string,
  }) }}
{% endmacro %}
