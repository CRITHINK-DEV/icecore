{#- icecore.dimension_anomaly: incremental per-value row counts of one dimension column (UC2). Documented in
    dimension_anomaly.yml.
    Reads the rows that changed since the previous observation (icecore.get_change_window), counts them per value of
    the tested column (icecore.compute_dimension_metrics: the `max_values` most frequent values, the rest grouped as
    other), emits one `window_rows` metric per value with dimension `<column>=<value>` and compares each with the
    static `bounds` {min, max}. Until UC3 the bounds are the only verdict.
    Pass or fail comes only from the bounds: the history read and the emits are guarded. -#}
{% test dimension_anomaly(model, column_name, bounds=none, max_values=none, since=none, timestamp_column=none, lookback_hours=24) %}
  {%- do icecore._icecore_validate_bounds({'window_rows': bounds} if bounds is not none else none, ['window_rows'], 'icecore.dimension_anomaly') -%}

  {%- set breaches = [] -%}
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
        {#- UC3 scoring hook: icecore.score(<history of this value's window_rows>, entry['window_rows']) plugs in
            here and its result goes to emit_metrics as `anomaly` of the entry. Not called at this stage (engine none). -#}
        {%- do to_emit.append({'metric': 'window_rows', 'value': entry['window_rows'], 'column': column_name,
            'dimension': column_name ~ '=' ~ label,
            'attributes': {'dbt.window.until': until, 'dbt.window.monitor': 'dimension_anomaly'}}) -%}
        {%- if icecore._icecore_outside(entry['window_rows'], bounds) -%}
          {%- do breaches.append({'dimension': column_name ~ '=' ~ label, 'value': entry['window_rows'], 'min': bounds.get('min'), 'max': bounds.get('max')}) -%}
        {%- endif -%}
      {%- endfor -%}
      {#- one CALL for the whole test -#}
      {%- do icecore.emit_metrics(model, to_emit) -%}
    {%- endif -%}
  {%- endif -%}

  {%- if breaches -%}
    {%- for b in breaches %}
  select {{ icecore._icecore_sql_literal(b['dimension']) }}::varchar as dimension, {{ b['value'] }}::number as window_rows,
         {{ 'null' if b['min'] is none else b['min'] }}::float as min_value, {{ 'null' if b['max'] is none else b['max'] }}::float as max_value
  {{ 'union all' if not loop.last }}
    {%- endfor -%}
  {%- else %}
  select null::varchar as dimension, null::number as window_rows, null::float as min_value, null::float as max_value where false
  {%- endif %}
{% endtest %}
