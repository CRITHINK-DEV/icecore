{#- icecore.column_anomaly: incremental column metrics test (UC2). Documented in column_anomaly.yml.
    Reads the rows that changed since the previous observation (icecore.get_change_window: CHANGES, or the
    timestamp_column fallback), computes the requested metrics over them (icecore.compute_column_metrics), emits one
    metric per value (column = the tested column) and compares each with the static `bounds`. Until UC3 the bounds
    are the only verdict. The previous observation is the newest metric record of this column of this relation, read
    through icecore.base_events(); with none, the window starts `lookback_hours` ago, and `since` overrides both.
    Pass or fail comes only from the bounds: the history read and the emits are guarded. -#}
{% test column_anomaly(model, column_name, metrics=none, bounds=none, since=none, timestamp_column=none, lookback_hours=24) %}
  {%- set names = icecore._icecore_column_metric_defaults() if metrics is none else metrics -%}
  {%- set bounds = {} if bounds is none else bounds -%}
  {%- if bounds is not mapping -%}
    {{ exceptions.raise_compiler_error("icecore.column_anomaly: bounds must map a metric to {min, max}, for example {null_rate: {max: 0.05}}") }}
  {%- endif -%}
  {%- for metric, limit in bounds.items() -%}
    {%- if metric not in names -%}
      {{ exceptions.raise_compiler_error("icecore.column_anomaly: bounds for " ~ metric ~ " but it is not in metrics " ~ names) }}
    {%- endif -%}
    {%- if limit is not mapping or not (limit.keys() | list) or (limit.keys() | list | reject('in', ['min', 'max']) | list) -%}
      {{ exceptions.raise_compiler_error("icecore.column_anomaly: bounds." ~ metric ~ " must be {min: n, max: n}, with at least one of them") }}
    {%- endif -%}
  {%- endfor -%}
  {%- set since = none if (since is none or (since | string | trim) == '') else since -%}

  {%- set breaches = [] -%}
  {%- if execute -%}
    {%- set name = icecore._icecore_quoted_name(model) -%}
    {%- set iso = 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"' -%}
    {%- if since is none -%}
      {#- previous observation of this column; any failure here means "none" -#}
      {%- set parameter_rows = run_query("show parameters like 'EVENT_TABLE' in database " ~ icecore._icecore_identifier(target.database)).rows -%}
      {%- set event_table = icecore.event_table_to_read(parameter_rows[0]['value'] if parameter_rows | length > 0 else none) -%}
      {%- if event_table is not none -%}
        {%- set previous_sql -%}
          select to_varchar(max(event_at), '{{ iso }}')
          from ({{ icecore.base_events(event_table=event_table) }})
          where record_name = 'icecore.metric' and node_relation = {{ icecore._icecore_sql_literal(name) }}
            and metric_column = {{ icecore._icecore_sql_literal(column_name) }}
        {%- endset -%}
        {%- set since = icecore._icecore_safe_scalar(previous_sql, 'column_anomaly previous observation', model) -%}
      {%- endif -%}
      {%- if since is none -%}
        {%- set since = run_query("select to_varchar(dateadd(second, -" ~ ((lookback_hours | float) * 3600) | int ~ ", sysdate()), '" ~ iso ~ "')").rows[0][0] -%}
      {%- endif -%}
    {%- endif -%}
    {%- set window = icecore.get_change_window(model, since, timestamp_column) -%}
    {%- set values = icecore.compute_column_metrics(model, column_name, window, names) -%}
    {%- if values is none -%}
      {%- do log('icecore: warning: column_anomaly of ' ~ column_name ~ ' on ' ~ name ~ ' could not be measured (see above); the test passes without metrics', info=True) -%}
    {%- else -%}
      {%- for metric, value in values.items() -%}
        {#- UC3 scoring hook: icecore.score(<history of this metric>, value) plugs in here and its result goes to
            emit_metric as `anomaly=`. Not called at this stage (engine none). -#}
        {%- do icecore.emit_metric(model, metric, value, column=column_name) -%}
        {%- set limit = bounds.get(metric) -%}
        {%- if limit is not none and value is not none -%}
          {%- if (limit.get('min') is not none and value < limit['min']) or (limit.get('max') is not none and value > limit['max']) -%}
            {%- do breaches.append({'metric': metric, 'value': value, 'min': limit.get('min'), 'max': limit.get('max')}) -%}
          {%- endif -%}
        {%- endif -%}
      {%- endfor -%}
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
