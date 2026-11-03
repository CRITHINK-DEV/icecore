{#- icecore.column_anomaly: incremental column metrics test (UC2). Documented in macros/tests/column_anomaly.yml (on dbt 1.8, a generic test's docs are read only from macro-paths).
    Reads the rows that changed since the previous observation (icecore.get_change_window: CHANGES, or the
    timestamp_column fallback), computes the requested metrics over them (icecore.compute_column_metrics), emits one
    metric per value (column = the tested column) and compares each with the static `bounds`. Until UC3 the bounds
    are the only verdict. The previous observation is the newest metric record of this column of this relation, read
    through icecore.base_events(), from the window end it recorded (`dbt.window.until`); with none, the window starts `lookback_hours` ago, and `since` overrides both.
    Pass or fail comes only from the bounds: the history read and the emits are guarded. -#}
{% test column_anomaly(model, column_name, metrics=none, bounds=none, since=none, timestamp_column=none, lookback_hours=24, window_tag=none) %}
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
        {#- UC3 scoring hook: icecore.score(<history of this metric>, value) plugs in here and its result goes to
            emit_metrics as `anomaly` of the entry. Not called at this stage (engine none). -#}
        {%- set attrs = {'dbt.window.until': until, 'dbt.window.monitor': 'column_anomaly'} -%}
        {%- if window_tag is not none -%}{%- do attrs.update({'dbt.window.tag': window_tag}) -%}{%- endif -%}
        {%- do to_emit.append({'metric': metric, 'value': value, 'column': column_name, 'attributes': attrs}) -%}
        {%- if icecore._icecore_outside(value, bounds.get(metric)) -%}
          {%- set limit = bounds.get(metric) -%}
          {%- do breaches.append({'metric': metric, 'value': value, 'min': limit.get('min'), 'max': limit.get('max')}) -%}
        {%- endif -%}
      {%- endfor -%}
      {#- one CALL for the whole test -#}
      {%- do icecore.emit_metrics(model, to_emit) -%}
    {%- endif -%}
  {%- endif -%}

  {%- if breaches -%}
    {%- for b in breaches %}
  select {{ icecore._icecore_sql_literal(b['metric']) }}::varchar as metric, {{ b['value'] }}::float as value,
         {{ 'null' if b['min'] is none else b['min'] }}::float as min_value, {{ 'null' if b['max'] is none else b['max'] }}::float as max_value
  {{ 'union all' if not loop.last }}
    {%- endfor -%}
  {%- else %}
  select null::varchar as metric, null::float as value, null::float as min_value, null::float as max_value where false
  {%- endif %}
{% endtest %}
