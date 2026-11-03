{#-
  UC1 on-run-start hook: `on-run-start: "{{ icecore.start_run() }}"`.

  Writes one record, the span event `icecore.run_start`, through icecore.emit (ICECORE_EMIT, guarded), with
  base_attributes() plus dbt.version, icecore.anomaly_engine and icecore.shadow_engine, so every run records
  what it ran with. Renders '' so the hook itself runs no SQL. A no-op at parse time and when
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
    dbt timings travel as attributes). -#}
{% macro _icecore_run_start_attributes() %}
  {%- set attrs = icecore._icecore_run_attributes() -%}
  {%- do attrs.update({'dbt.invocation.started_at': icecore._icecore_iso(run_started_at)}) -%}
  {{ return(attrs) }}
{% endmacro %}


{#- What the run ran with, on the start record and the invocation-end record (spec section 4). -#}
{% macro _icecore_run_attributes() %}
  {{ return({
      'dbt.version': dbt_version | string,
      'icecore.anomaly_engine': icecore._icecore_setting('anomaly_engine', 'auto') | string,
      'icecore.shadow_engine': icecore._icecore_setting('shadow_engine', 'none') | string,
  }) }}
{% endmacro %}
