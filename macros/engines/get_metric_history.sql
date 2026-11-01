{#-
  UC3 lag-aware metric history reader (spec section 6). Reads the history of ONE metric series, the key
  (relation, metric, column, dimension), from the `metrics` view of the obs schema (macros/views/metrics.sql),
  in the shape icecore.score expects: a SELECT returning `observed_at` (timestamp) and `value` (number).

  Window, on Snowflake's clock in UTC (the event table's):
    observed_at >  now - lookback_days            (default 30 days)
    observed_at <= now - history delay            (var icecore.history_delay_minutes, default 1)
  The delay is the lag rule. Ingestion lag is seconds (spike S3: p95 about 8 s, worst 20 s), so a record newer than
  the delay may be missing or partial, and a history that includes some of those would skew a band. OBSERVED_TIMESTAMP
  is NULL on icecore records (S3), so the rule is applied on `observed_at`, which the metrics view takes from the
  event's TIMESTAMP. Fewer than min_points (default 7) points in the window is "insufficient history": the result
  says so and score never flags such a series (native baseline insufficient_history).

  Use:
    {%- set history = icecore.get_metric_history(model, 'row_count') -%}
    {%- set result = icecore.score(history['sql'], value) -%}
-#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {sql, points, min_points, insufficient_history, lookback_days, delay_minutes}. `sql` is the series
    for score; `points` counts the rows of the window. At parse time nothing runs: points and insufficient_history
    are none. -#}
{% macro get_metric_history(relation, metric, column=none, dimension=none, lookback_days=30, delay_minutes=none,
                            min_points=7, source=none, schema=none) %}
  {%- set delay = icecore._icecore_history_delay(delay_minutes) -%}
  {%- set sql = icecore.get_metric_history_sql(relation, metric, column, dimension, lookback_days, delay, source, schema) -%}
  {%- set out = {'sql': sql, 'points': none, 'min_points': min_points, 'insufficient_history': none,
                 'lookback_days': lookback_days, 'delay_minutes': delay} -%}
  {%- if execute -%}
    {%- set points = run_query('select count(*) from (' ~ sql ~ ') as history').columns[0].values()[0] | int -%}
    {%- do out.update({'points': points, 'insufficient_history': points < min_points}) -%}
    {%- if points < min_points -%}
      {%- do log('icecore: insufficient history for ' ~ metric ~ ': ' ~ points ~ ' point(s) in ' ~ lookback_days
          ~ ' days, at least ' ~ min_points ~ ' needed', info=True) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- The series SELECT alone, no query run. `delay_minutes` is taken as given here (get_metric_history resolves the
    var). `source` is the relation to read, default the obs schema's METRICS view. -#}
{% macro get_metric_history_sql(relation, metric, column=none, dimension=none, lookback_days=30, delay_minutes=1,
                                source=none, schema=none) %}
  {%- if lookback_days is not integer or lookback_days < 1 -%}
    {{ exceptions.raise_compiler_error('icecore: lookback_days must be an integer of at least 1, got ' ~ lookback_days) }}
  {%- endif -%}
  {%- if delay_minutes is not integer or delay_minutes < 0 -%}
    {{ exceptions.raise_compiler_error('icecore: history delay must be an integer of at least 0 minutes, got ' ~ delay_minutes) }}
  {%- endif -%}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set source = source or icecore._icecore_procedure('METRICS', schema) -%}
  {%- set literal = icecore._icecore_sql_literal -%}
  {%- set now = "convert_timezone('UTC', current_timestamp())::timestamp_ntz" -%}
  {%- set sql -%}
select observed_at, value
from {{ source }}
where relation = {{ literal(name) }}
  and metric = {{ literal(metric | string) }}
  and "COLUMN" is not distinct from {{ 'null' if column is none else literal(column | string) }}
  and dimension is not distinct from {{ 'null' if dimension is none else literal(dimension | string) }}
  and value is not null
  and observed_at > dateadd(day, -{{ lookback_days }}, {{ now }})
  and observed_at <= dateadd(minute, -{{ delay_minutes }}, {{ now }})
order by observed_at
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The history delay in minutes: the argument, else var icecore.history_delay_minutes (default 1, spike S3). -#}
{% macro _icecore_history_delay(delay_minutes=none) %}
  {{ return(var('icecore', {}).get('history_delay_minutes', 1) if delay_minutes is none else delay_minutes) }}
{% endmacro %}
