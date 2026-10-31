{#-
  Spike S3: ingestion lag, concurrency and hook coverage. Probe code for the sandbox only.

  Emission follows Gate 1 (spikes/GATE1.md, option B): a persisted SQL procedure in the style of
  ICECORE_EMIT, here <target db>.<target schema>.ICECORE_EMIT_S3_<run_id>, CALLed through the guard
  block (s1_emit_call) and dropped by the test. Unlike the S1 probe, it stamps the events of a CALL
  with `icecore.emitted_at` from Snowflake's clock (SYSDATE(), UTC), read once when the CALL starts, so
  lag is measured on one clock: the laptop runs about 10.7 s ahead of Snowflake (S4.md). Reading
  SYSDATE() per event instead cost about 120 ms per event (a query per iteration), 12 s per CALL of 100.

  Hooks (integration_tests/dbt_project.yml) call s3_hook_probe only when var('s3_probe') is true.
-#}

{% macro s3_proc_name(run_id) -%}
  {{- return(target.database ~ '.' ~ target.schema ~ '.ICECORE_EMIT_S3_' ~ (run_id | upper)) -}}
{%- endmacro %}


{% macro s3_emit_proc_create(run_id) %}
  {% if execute %}
    {% set sql %}
CREATE OR REPLACE PROCEDURE {{ s3_proc_name(run_id) }}(events ARRAY)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
AS $$
DECLARE
  nm STRING;
  a OBJECT;
  ts STRING;
BEGIN
  ts := TO_VARCHAR(SYSDATE(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6') || 'Z';
  FOR i IN 0 TO ARRAY_SIZE(events) - 1 DO
    nm := events[i]:name::STRING;
    a := OBJECT_INSERT(events[i]:attrs::OBJECT, 'icecore.emitted_at', ts, TRUE);
    SYSTEM$ADD_EVENT(:nm, :a);
  END FOR;
  RETURN 'ok ' || ARRAY_SIZE(events);
END;
$$
    {% endset %}
    {% do run_query(sql) %}
  {% endif %}
{% endmacro %}


{#- One record for an on-run-start / on-run-end hook. Returns '' so the hook itself runs no SQL. -#}
{% macro s3_hook_probe(phase) %}
  {% if execute %}
    {% set rid = var('run_id') %}
    {% set attrs = {
      'icecore.run_id': rid,
      'icecore.phase': phase,
      'dbt.command': flags.WHICH,
      'dbt.invocation_id': invocation_id,
      'dbt.version': dbt_version,
    } %}
    {% do s1_emit_call(s3_proc_name(rid), [{'name': 'icecore.s3_hook', 'attrs': attrs}]) %}
  {% endif %}
  {{ return('') }}
{% endmacro %}


{#- The events of one emitter, cut into CALLs of at most `batch` (a CALL keeps only its last 128, S2.md). -#}
{% macro _s3_burst_batches(run_id, n, emitter=0, batch=100) %}
  {% set batches = [] %}
  {% for first in range(1, (n | int) + 1, batch | int) %}
    {% set events = [] %}
    {% for seq in range(first, [first + (batch | int), (n | int) + 1] | min) %}
      {% do events.append({'name': 'icecore.s3_burst', 'attrs': {
        'icecore.run_id': run_id, 'icecore.emitter': emitter | int, 'icecore.seq': seq,
        'dbt.invocation_id': invocation_id}}) %}
    {% endfor %}
    {% do batches.append(events) %}
  {% endfor %}
  {{ return(batches) }}
{% endmacro %}


{#- Offline check of the chunking: logs the batch sizes. -#}
{% macro s3_burst_plan(run_id, n, emitter=0) %}
  {{ log("icecore_it_result:" ~ tojson(_s3_burst_batches(run_id, n, emitter) | map('length') | list), info=True) }}
{% endmacro %}


{#- n records for run_id, from one emitter, in CALLs of 100. Logs `icecore_s3_burst:{statuses, wall_s}`. -#}
{% macro s3_burst(run_id, n, emitter=0) %}
  {% if execute %}
    {% set t0 = modules.datetime.datetime.now() %}
    {% set statuses = [] %}
    {% for events in _s3_burst_batches(run_id, n, emitter) %}
      {% do statuses.append(s1_emit_call(s3_proc_name(run_id), events)) %}
    {% endfor %}
    {% set wall = (modules.datetime.datetime.now() - t0).total_seconds() %}
    {{ log("icecore_s3_burst:" ~ tojson({'emitter': emitter | int, 'statuses': statuses, 'wall_s': wall}), info=True) }}
  {% endif %}
{% endmacro %}
