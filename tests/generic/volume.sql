{#- icecore.volume: zero-scan volume test (UC2). Documented in volume.yml.
    Reads the total row count with icecore.get_row_count_metadata (INFORMATION_SCHEMA.TABLES, no data scan),
    scores it against the metric's own history (icecore.score_monitor_metrics: one query), emits it as the metric
    row_count with the scored result, then fails when it is outside min_rows / max_rows OR the engine says anomaly.
    Not enough history: not scored, the test passes on that account. emit_metric guards its own CALL. -#}
{% test volume(model, min_rows=none, max_rows=none, engine=none, sensitivity=none, min_consecutive=none, lookback_days=30, min_points=7, shadow_engine=none) %}
  {{ config(severity='error', warn_if='>=1', error_if=icecore._icecore_error_if()) }}
  {%- for pair in [('min_rows', min_rows), ('max_rows', max_rows)] -%}
    {%- if pair[1] is not none and (pair[1] is boolean or pair[1] is not number or pair[1] < 0) -%}
      {{ exceptions.raise_compiler_error("icecore.volume: " ~ pair[0] ~ " must be a non-negative number; got '" ~ pair[1] ~ "'") }}
    {%- endif -%}
  {%- endfor -%}
  {%- if min_rows is not none and max_rows is not none and min_rows > max_rows -%}
    {{ exceptions.raise_compiler_error("icecore.volume: min_rows (" ~ min_rows ~ ") is above max_rows (" ~ max_rows ~ ")") }}
  {%- endif -%}

  {%- set breach = false -%}
  {%- set unmeasured = none -%}
  {%- set row_count = none -%}
  {%- set verdict = none -%}
  {%- do icecore.score_monitor_metrics(model, [], engine, sensitivity, min_consecutive, lookback_days, min_points, shadow_engine) -%}
  {%- if execute -%}
    {%- set row_count = icecore.get_row_count_metadata(model) -%}
    {%- if row_count is none -%}
      {#- A view, an external table, an Iceberg table, or not readable (the reader logged why): nothing to judge. -#}
      {%- do log('icecore: warning: volume of ' ~ model ~ ' could not be measured from metadata', info=True) -%}
      {#- No threshold: nothing to judge, the test passes. With min_rows or max_rows: one failing row, so the test's severity applies. -#}
      {%- if min_rows is not none or max_rows is not none -%}
        {%- set unmeasured = 'unmeasured: no trusted metadata row count (view, external or Iceberg table, missing, or not readable)' -%}
      {%- endif -%}
    {%- else -%}
      {%- set breach = (min_rows is not none and row_count < min_rows) or (max_rows is not none and row_count > max_rows) -%}
      {%- set verdict = icecore.score_monitor_metrics(model, [{'metric': 'row_count', 'value': row_count}], engine, sensitivity, min_consecutive, lookback_days, min_points, shadow_engine)[0] -%}
      {%- do icecore.emit_metric(model, 'row_count', row_count, anomaly=verdict, attributes=icecore._icecore_metric_attrs(verdict)) -%}
    {%- endif -%}
  {%- endif -%}

  {%- set anomalous = icecore._icecore_anomalous(verdict) -%}
  {#- dbt counts rows: a hard breach (or an unmeasured metric with a threshold) returns 2, an anomaly alone 1 (see _icecore_error_if) -#}
  {%- set failing_rows = 2 if (breach or unmeasured is not none) else (1 if anomalous else 0) -%}
  select {{ 'null' if row_count is none else row_count }}::number as row_count,
         {{ 'null' if min_rows is none else min_rows }}::number as min_rows,
         {{ 'null' if max_rows is none else max_rows }}::number as max_rows,
         {{ 'null' if (unmeasured is none and not anomalous) else icecore._icecore_sql_literal(unmeasured if unmeasured is not none else icecore._icecore_anomaly_text(verdict)) }}::varchar as reason
  from (select 1 as k union all select 2 as k) where k <= {{ failing_rows }}
{% endtest %}
