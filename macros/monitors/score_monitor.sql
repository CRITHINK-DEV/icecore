{#- UC3 scoring for the generic monitor tests (freshness, volume, column_anomaly, all_columns_anomaly, dimension_anomaly).
    Documented in score_monitor.yml.

    score_monitor_metrics(relation, points, ...) scores the metrics a test just computed against their own history and returns
    one result per point, in the order given: the contract of icecore.score (engine, expected, lower, upper, score,
    is_anomaly, fallback_reason, ...) to pass to emit_metric(s) as `anomaly`, or none when the point is not scored (not
    enough history, scoring off, or a failed read). A test fails (or warns, per its severity) on is_anomaly true.

    Cost: ONE query reads and scores the history of every metric of the test (the history SELECTs of
    get_metric_history_sql, the lag-aware reader's window, one scored subquery per point, chunked at 25 points so the
    statement stays far under Snowflake's size limit). freshness, volume, column_anomaly with the default metrics up to 25
    values: 1 query; all_columns_anomaly and dimension_anomaly: ceil(points / 25). Not enough history is read from that same
    query (baseline insufficient_history), never from a count of its own. Only when the engine is cortex or auto AND a
    series has at least 14 days of history does a point cost more (the dispatcher's guarded Cortex call: 2 queries each,
    a fallback to the native result when it cannot score). A shadow engine costs one more query per point (native) or the
    Cortex calls, and writes its own icecore.shadow event. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro score_monitor_metrics(relation, points, engine=none, sensitivity=none, min_consecutive=none, lookback_days=30, min_points=7, shadow_engine=none, schema=none, runner=none) %}
  {%- set settings = icecore._anomaly_settings(engine, sensitivity, min_consecutive) -%}
  {%- set shadow = icecore._shadow_engine(shadow_engine) -%}
  {%- if lookback_days is not integer or lookback_days is boolean or lookback_days < 1 -%}
    {{ exceptions.raise_compiler_error('icecore: lookback_days must be an integer of at least 1, got ' ~ lookback_days) }}
  {%- endif -%}
  {%- if min_points is not integer or min_points is boolean or min_points < 1 -%}
    {{ exceptions.raise_compiler_error('icecore: min_points must be an integer of at least 1, got ' ~ min_points) }}
  {%- endif -%}
  {%- set unscored = [] -%}
  {%- for p in points -%}{%- do unscored.append(none) -%}{%- endfor -%}
  {%- if not execute or not points or not icecore._icecore_flag('monitor_score', true) -%}{{ return(unscored) }}{%- endif -%}
  {%- set scored = {} -%}

  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set delay = icecore._icecore_history_delay(none) -%}
  {%- set series_sql = [] -%}
  {%- set values = [] -%}
  {%- for p in points -%}
    {%- do series_sql.append(icecore.get_metric_history_sql(name, p['metric'], p.get('column'), p.get('dimension'), lookback_days, delay, none, schema)) -%}
    {%- do values.append(icecore._icecore_metric_number(p['value'])) -%}
  {%- endfor -%}

  {#- the one query (per chunk): every point's native score, which also reports its history size -#}
  {%- set native = {} -%}
  {%- set indexes = [] -%}
  {%- for i in range(points | length) if values[i] is not none -%}{%- do indexes.append(i) -%}{%- endfor -%}
  {%- for start in range(0, indexes | length, 25) -%}
    {%- set parts = [] -%}
    {%- for i in indexes[start:start + 25] -%}
      {%- do parts.append('select ' ~ i ~ ' as i, object_construct(*) as r from (' ~ icecore.native__score_sql(series_sql[i], values[i], settings.sensitivity, settings.min_consecutive, none, min_points) ~ ')') -%}
    {%- endfor -%}
    {%- set statement = 'select object_agg(i::string, r) from (' ~ (parts | join(' union all ')) ~ ')' -%}
    {%- set res = runner(statement) if runner else icecore._icecore_try_scalar(statement) -%}
    {%- if 'error' in res -%}
      {%- do log('icecore: warning: anomaly scoring of ' ~ name ~ ' failed (' ~ res['error'] ~ '); its metrics are not scored', info=True) -%}
    {%- else -%}
      {%- do native.update(res.get('value') or {}) -%}
    {%- endif -%}
  {%- endfor -%}

  {%- set insufficient = [] -%}
  {%- for i in indexes if (i | string) in native -%}
    {%- set row = {} -%}
    {%- for key, value in native[i | string].items() -%}{%- do row.update({key | lower: value}) -%}{%- endfor -%}
    {%- if row.get('baseline') == 'insufficient_history' -%}
      {%- do insufficient.append(points[i]['metric']) -%}
    {%- else -%}
      {%- set result = {'engine': 'native', 'fallback_reason': none, 'is_anomaly': row.get('is_anomaly') == true,
                        'baseline': row.get('baseline'), 'history_points': row.get('history_points') | int,
                        'history_days': row.get('history_days') | int} -%}
      {%- for field in ['expected', 'lower', 'upper', 'score'] -%}
        {%- do result.update({field: none if row.get(field) is none else row[field] | float}) -%}
      {%- endfor -%}
      {%- if settings.engine != 'native' -%}
        {%- if result['history_days'] < 14 -%}
          {%- do result.update({'fallback_reason': 'history_too_short'}) -%}
        {%- else -%}
          {%- set p = points[i] -%}
          {%- set attempt = icecore._cortex_attempt(icecore.cortex_model_name(name), icecore.cortex_series_key(p['metric'], p.get('column'), p.get('dimension')),
                                                   series_sql[i], values[i], settings, none, none, schema) -%}
          {%- if attempt.get('result') is not none -%}
            {%- set result = attempt['result'] -%}
            {%- do result.update({'fallback_reason': none}) -%}
          {%- else -%}
            {%- do result.update({'fallback_reason': attempt.get('fallback_reason')}) -%}
          {%- endif -%}
        {%- endif -%}
        {%- if result.get('fallback_reason') is not none -%}
          {%- do log("icecore: anomaly engine '" ~ settings.engine ~ "' scored " ~ points[i]['metric'] ~ " of " ~ name ~ " with native (" ~ result.get('fallback_reason') ~ ")") -%}
        {%- endif -%}
      {%- endif -%}
      {#- the history window the verdict rests on, for the metric's attributes (the anomaly event carries it): dbt.window.* -#}
      {%- do result.update({'window_attributes': {'dbt.window.history_points': result.get('history_points'), 'dbt.window.history_days': result.get('history_days'),
                                                  'dbt.window.lookback_days': lookback_days}}) -%}
      {%- do scored.update({i: result}) -%}
      {%- if shadow != 'none' -%}
        {%- set p = points[i] -%}
        {%- set sh = icecore.shadow_score(series_sql[i], values[i], result, settings.sensitivity, settings.min_consecutive, none,
                                          icecore.cortex_model_name(name), icecore.cortex_series_key(p['metric'], p.get('column'), p.get('dimension')),
                                          shadow, none, min_points, schema) -%}
        {%- do icecore.emit_shadow(name, p['metric'], values[i], sh, result, p.get('column'), p.get('dimension')) -%}
      {%- endif -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if insufficient -%}
    {%- do log('icecore: insufficient history on ' ~ name ~ ' for ' ~ (insufficient | unique | list | length) ~ ' metric(s) (' ~ (insufficient | unique | list | join(', ')) ~ '): fewer than ' ~ min_points ~ ' points in ' ~ lookback_days ~ ' days; not scored, the test is not failed for it', info=True) -%}
  {%- endif -%}
  {%- set results = [] -%}
  {%- for i in range(points | length) -%}{%- do results.append(scored.get(i)) -%}{%- endfor -%}
  {{ return(results) }}
{% endmacro %}


{#- The dbt.window.* attributes of a scored result ({} for none), to pass to emit_metric. -#}
{% macro _icecore_window_attrs(result) %}
  {{ return({} if result is none else result.get('window_attributes', {})) }}
{% endmacro %}


{#- True when a result (or none) says is_anomaly true. Used by the tests. -#}
{% macro _icecore_anomalous(result) %}
  {{ return(result is not none and result.get('is_anomaly') is sameas true) }}
{% endmacro %}


{#- Short text of a breach for a failing row. -#}
{% macro _icecore_anomaly_text(result) %}
  {{ return('anomaly (' ~ result.get('engine') ~ '): outside the expected band [' ~ result.get('lower') ~ ', ' ~ result.get('upper') ~ '] around ' ~ result.get('expected')) }}
{% endmacro %}
