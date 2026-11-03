{#- icecore.dimension_anomaly: incremental per-value row counts of one dimension column (UC2). Documented in
    dimension_anomaly.yml.
    Reads the rows that changed since the previous observation (icecore.get_change_window), counts them per value of
    the tested column (icecore.compute_dimension_metrics: the `max_values` most frequent values, the rest grouped as
    other), emits one `window_rows` metric per value with dimension `<column>=<value>` and compares each with the
    static `bounds` {min, max}, and scores each against its own history (icecore.score_monitor_metrics: one query per 25
    values). Fails on a bound breach OR an anomaly; not enough history: not scored. The history read, the scoring and the
    emits are guarded. -#}
{% test dimension_anomaly(model, column_name, bounds=none, max_values=none, since=none, timestamp_column=none, lookback_hours=24, engine=none, sensitivity=none, min_consecutive=none, lookback_days=30, min_points=7, shadow_engine=none) %}
  {{ config(severity='error', warn_if='>=1', error_if=icecore._icecore_error_if()) }}
  {%- do icecore._icecore_validate_bounds({'window_rows': bounds} if bounds is not none else none, ['window_rows'], 'icecore.dimension_anomaly') -%}

  {%- set breaches = [] -%}
  {%- set anomalies = [] -%}
  {%- if execute -%}
    {%- set name = icecore._icecore_quoted_name(model) -%}
    {#- the window end is taken before the read and recorded on every metric, so the next window starts from it -#}
    {%- set until = icecore._icecore_now_iso() -%}
    {%- set since = icecore._icecore_window_since(model, 'dimension', column_name, since, lookback_hours) -%}
    {%- set window = icecore.get_change_window(model, since, timestamp_column, until) -%}
    {%- set values = icecore.compute_dimension_metrics(model, column_name, window, max_values) -%}
    {%- if values is none -%}
      {%- do log('icecore: warning: dimension_anomaly of ' ~ column_name ~ ' on ' ~ name ~ ' could not be measured (see above); the test passes without metrics', info=True) -%}
    {%- else -%}
      {%- set to_emit = [] -%}
      {%- for entry in values -%}
        {%- set label = '<other>' if entry['other'] else ('<null>' if entry['value'] is none else entry['value']) -%}
        {%- do to_emit.append({'metric': 'window_rows', 'value': entry['window_rows'], 'column': column_name,
            'dimension': column_name ~ '=' ~ label,
            'attributes': {'dbt.window.until': until, 'dbt.window.monitor': 'dimension_anomaly'}}) -%}
        {%- if icecore._icecore_outside(entry['window_rows'], bounds) -%}
          {%- do breaches.append({'dimension': column_name ~ '=' ~ label, 'value': entry['window_rows'], 'min': bounds.get('min'), 'max': bounds.get('max'), 'reason': 'outside bounds'}) -%}
        {%- endif -%}
      {%- endfor -%}
      {%- set scores = icecore.score_monitor_metrics(model, to_emit, engine, sensitivity, min_consecutive, lookback_days, min_points, shadow_engine) -%}
      {%- for entry in to_emit -%}
        {%- set result = scores[loop.index0] -%}
        {%- if result is not none -%}{%- do entry.update({'anomaly': result}) -%}{%- endif -%}
        {%- do entry['attributes'].update(icecore._icecore_metric_attrs(result)) -%}
        {%- if icecore._icecore_anomalous(result) and not (breaches | selectattr('dimension', 'equalto', entry['dimension']) | list) -%}
          {%- do anomalies.append({'dimension': entry['dimension'], 'value': entry['value'], 'min': result.get('lower'), 'max': result.get('upper'), 'reason': icecore._icecore_anomaly_text(result)}) -%}
        {%- endif -%}
      {%- endfor -%}
      {#- one CALL for the whole test -#}
      {%- do icecore.emit_metrics(model, to_emit) -%}
    {%- endif -%}
  {%- endif -%}

  {#- dbt counts rows (see _icecore_error_if): hard breaches one row each, padded to at least 2; the anomalies together one row -#}
  {%- set failing = [] -%}
  {%- do failing.extend(breaches) -%}
  {%- if anomalies -%}
    {%- do anomalies[0].update({'reason': anomalies[0]['reason'] ~ ((' (and ' ~ (anomalies | length - 1) ~ ' more)') if anomalies | length > 1 else '')}) -%}
    {%- do failing.append(anomalies[0]) -%}
  {%- endif -%}
  {%- if breaches and failing | length < 2 -%}{%- do failing.append(breaches[0]) -%}{%- endif -%}
  {%- if failing -%}
    {%- for b in failing %}
  select {{ icecore._icecore_sql_literal(b['dimension']) }}::varchar as dimension, {{ b['value'] }}::number as window_rows,
         {{ 'null' if b['min'] is none else b['min'] }}::float as min_value, {{ 'null' if b['max'] is none else b['max'] }}::float as max_value,
         {{ icecore._icecore_sql_literal(b['reason']) }}::varchar as reason
  {{ 'union all' if not loop.last }}
    {%- endfor -%}
  {%- else %}
  select null::varchar as dimension, null::number as window_rows, null::float as min_value, null::float as max_value, null::varchar as reason where false
  {%- endif %}
{% endtest %}
