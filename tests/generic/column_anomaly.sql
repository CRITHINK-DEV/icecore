{#- icecore.column_anomaly: incremental column metrics test (UC2). Documented in column_anomaly.yml.
    Reads the rows that changed since the previous observation (icecore.get_change_window: CHANGES, or the
    timestamp_column fallback), computes the requested metrics over them (icecore.compute_column_metrics), emits one
    metric per value (column = the tested column) and compares each with the static `bounds`. Each value is also scored
    against its own history (icecore.score_monitor_metrics: one query for the whole test) and the test fails on an anomaly too. The previous observation is the newest metric record of this column of this relation, read
    through icecore.base_events(), from the window end it recorded (`dbt.window.until`); with none, the window starts `lookback_hours` ago, and `since` overrides both.
    Not enough history: not scored. The history read, the scoring and the emits are guarded: they never fail a run by themselves. -#}
{% test column_anomaly(model, column_name, metrics=none, bounds=none, since=none, timestamp_column=none, lookback_hours=24, window_tag=none, engine=none, sensitivity=none, min_consecutive=none, lookback_days=30, min_points=7, shadow_engine=none) %}
  {%- set names = icecore._icecore_column_metric_defaults() if metrics is none else metrics -%}
  {%- do icecore._icecore_validate_bounds(bounds, names, 'icecore.column_anomaly') -%}
  {%- do icecore._icecore_validate_window_tag(window_tag) -%}
  {%- set bounds = bounds or {} -%}

  {%- set breaches = [] -%}
  {%- if execute -%}
    {%- set name = icecore._icecore_quoted_name(model) -%}
    {#- the window end is taken before the read and recorded on every metric, so the next window starts from it -#}
    {%- set until = icecore._icecore_now_iso() -%}
    {%- set since = icecore._icecore_window_since(model, 'column', column_name, since, lookback_hours, window_tag) -%}
    {%- set window = icecore.get_change_window(model, since, timestamp_column, until) -%}
    {%- set values = icecore.compute_column_metrics(model, column_name, window, names) -%}
    {%- if values is none -%}
      {%- do log('icecore: warning: column_anomaly of ' ~ column_name ~ ' on ' ~ name ~ ' could not be measured (see above); the test passes without metrics', info=True) -%}
    {%- else -%}
      {%- set to_emit = [] -%}
      {%- for metric, value in values.items() -%}
        {%- set attrs = {'dbt.window.until': until, 'dbt.window.monitor': 'column_anomaly'} -%}
        {%- if window_tag is not none -%}{%- do attrs.update({'dbt.window.tag': window_tag}) -%}{%- endif -%}
        {%- do to_emit.append({'metric': metric, 'value': value, 'column': column_name, 'attributes': attrs}) -%}
        {%- if icecore._icecore_outside(value, bounds.get(metric)) -%}
          {%- set limit = bounds.get(metric) -%}
          {%- do breaches.append({'metric': metric, 'value': value, 'min': limit.get('min'), 'max': limit.get('max'), 'reason': 'outside bounds'}) -%}
        {%- endif -%}
      {%- endfor -%}
      {%- set scores = icecore.score_monitor_metrics(model, to_emit, engine, sensitivity, min_consecutive, lookback_days, min_points, shadow_engine) -%}
      {%- for entry in to_emit -%}
        {%- set result = scores[loop.index0] -%}
        {%- if result is not none -%}{%- do entry.update({'anomaly': result}) -%}{%- endif -%}
        {%- do entry['attributes'].update(icecore._icecore_metric_attrs(result)) -%}
        {%- if icecore._icecore_anomaly_fails(result, entry['metric'] ~ ' of ' ~ name) and not (breaches | selectattr('metric', 'equalto', entry['metric']) | list) -%}
          {%- do breaches.append({'metric': entry['metric'], 'value': entry['value'], 'min': result.get('lower'), 'max': result.get('upper'), 'reason': icecore._icecore_anomaly_text(result)}) -%}
        {%- endif -%}
      {%- endfor -%}
      {#- one CALL for the whole test -#}
      {%- do icecore.emit_metrics(model, to_emit) -%}
    {%- endif -%}
  {%- endif -%}

  {%- if breaches -%}
    {%- for b in breaches %}
  select {{ icecore._icecore_sql_literal(b['metric']) }}::varchar as metric, {{ b['value'] }}::float as value,
         {{ 'null' if b['min'] is none else b['min'] }}::float as min_value, {{ 'null' if b['max'] is none else b['max'] }}::float as max_value,
         {{ icecore._icecore_sql_literal(b['reason']) }}::varchar as reason
  {{ 'union all' if not loop.last }}
    {%- endfor -%}
  {%- else %}
  select null::varchar as metric, null::float as value, null::float as min_value, null::float as max_value, null::varchar as reason where false
  {%- endif %}
{% endtest %}
