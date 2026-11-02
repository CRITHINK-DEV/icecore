{#- icecore.all_columns_anomaly: the default column metrics for every column of the relation in one test (UC2).
    Documented in all_columns_anomaly.yml.
    The columns come from icecore.get_columns_signature (SHOW COLUMNS) minus `exclude_columns`. One change window is
    opened (icecore.get_change_window) and each column's metrics are computed over it (icecore.compute_column_metrics,
    one query per column, over the changed rows only), emitted with icecore.emit_metric (one per metric, with the
    column) and compared with the static `bounds`, which apply to every column that has the metric. Metrics that
    need a numeric column are skipped, with a logged warning, on the others. Until UC3 the bounds are the only verdict.
    Pass or fail comes only from the bounds: the history read and the emits are guarded. -#}
{% test all_columns_anomaly(model, exclude_columns=none, metrics=none, bounds=none, since=none, timestamp_column=none, lookback_hours=24) %}
  {%- set names = icecore._icecore_column_metric_defaults() if metrics is none else metrics -%}
  {%- do icecore._icecore_validate_bounds(bounds, names, 'icecore.all_columns_anomaly') -%}
  {%- set bounds = bounds or {} -%}
  {%- set excluded = [] -%}
  {%- for c in ([exclude_columns] if exclude_columns is string else (exclude_columns or [])) -%}
    {%- do excluded.append(icecore._icecore_column_name(c)) -%}
  {%- endfor -%}

  {%- set breaches = [] -%}
  {%- if execute -%}
    {%- set name = icecore._icecore_quoted_name(model) -%}
    {%- set signature = icecore.get_columns_signature(model) -%}
    {%- if signature is none -%}
      {%- do log('icecore: warning: all_columns_anomaly of ' ~ name ~ ' could not be measured: its columns are not readable; the test passes', info=True) -%}
    {%- else -%}
      {#- the window end is taken before the read and recorded on every metric, so the next window starts from it -#}
      {%- set until = icecore._icecore_now_iso() -%}
      {%- set since = icecore._icecore_window_since(model, 'all_columns', none, since, lookback_hours) -%}
      {%- set window = icecore.get_change_window(model, since, timestamp_column, until) -%}
      {%- set to_emit = [] -%}
      {%- for c in signature if c['name'] not in excluded -%}
        {#- TODO(#75): use icecore._sql(c['name'], 'name') once #75 is merged. It quotes the exact stored name; the
            existing column helper reads a plain-looking name as upper case, so a case-sensitive column is not read
            until then. -#}
        {%- set values = icecore.compute_column_metrics(model, icecore._icecore_column_ref(c['name']), window, names) -%}
        {%- if values is none -%}
          {%- do log('icecore: warning: all_columns_anomaly of ' ~ c['name'] ~ ' on ' ~ name ~ ' could not be measured (see above)', info=True) -%}
        {%- else -%}
          {%- for metric, value in values.items() -%}
            {#- UC3 scoring hook: icecore.score(<history of this metric>, value) plugs in here and its result goes
                to emit_metrics as `anomaly` of the entry. Not called at this stage (engine none). -#}
            {%- do to_emit.append({'metric': metric, 'value': value, 'column': c['name'],
                'attributes': {'dbt.window.until': until, 'dbt.window.monitor': 'all_columns_anomaly'}}) -%}
            {%- if icecore._icecore_outside(value, bounds.get(metric)) -%}
              {%- set limit = bounds.get(metric) -%}
              {%- do breaches.append({'column': c['name'], 'metric': metric, 'value': value, 'min': limit.get('min'), 'max': limit.get('max')}) -%}
            {%- endif -%}
          {%- endfor -%}
        {%- endif -%}
      {%- endfor -%}
      {#- one CALL for the whole test (emit_events chunks at 100 events) -#}
      {%- do icecore.emit_metrics(model, to_emit) -%}
    {%- endif -%}
  {%- endif -%}

  {%- if breaches -%}
    {%- for b in breaches %}
  select {{ icecore._icecore_sql_literal(b['column']) }}::varchar as column_name, {{ icecore._icecore_sql_literal(b['metric']) }}::varchar as metric,
         {{ b['value'] }}::float as value, {{ 'null' if b['min'] is none else b['min'] }}::float as min_value, {{ 'null' if b['max'] is none else b['max'] }}::float as max_value
  {{ 'union all' if not loop.last }}
    {%- endfor -%}
  {%- else %}
  select null::varchar as column_name, null::varchar as metric, null::float as value, null::float as min_value, null::float as max_value where false
  {%- endif %}
{% endtest %}
