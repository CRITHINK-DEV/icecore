{#-
  Native engine (spec section 6): pure SQL, always available, one query over the history.

  Baseline: when the series spans more than four weeks and the current observation's
  (day-of-week, hour-of-day) bucket holds at least 4 points, the band is computed within that
  bucket (seasonal); otherwise over the whole series (flat). Fewer than 7 points never flags.
  Band: median ± k × MAD, k = 4 (low), 3 (medium), 2 (high); raw MAD, not scaled to a standard deviation,
  floored at greatest(anomaly_mad_floor_pct × |median|, anomaly_mad_floor_abs) so a constant series
  does not get a zero-width band. A median of exactly 0 (a null rate that was always 0) keeps a zero
  floor by default: any move off 0 is an anomaly.
  Score: signed distance outside the band, 0 inside.
  is_anomaly: score <> 0 and the min_consecutive - 1 latest history points were also outside
  the band of their own bucket, on the same side.
  Timestamps are UTC (as in the event table); observed_at defaults to the current UTC time.
-#}

{% macro native__score(series_sql, current_value, sensitivity, min_consecutive, observed_at=none) %}
  {% if not execute %}{{ return(none) }}{% endif %}
  {% set table = run_query(icecore.native__score_sql(series_sql, current_value, sensitivity, min_consecutive, observed_at)) %}
  {% set row = {} %}
  {% for name in table.column_names %}
    {% do row.update({name | lower: table.rows[0][loop.index0]}) %}
  {% endfor %}
  {% set result = {} %}
  {% for name in ['expected', 'lower', 'upper', 'score'] %}
    {% do result.update({name: none if row[name] is none else row[name] | float}) %}
  {% endfor %}
  {% do result.update({
    'engine': 'native',
    'is_anomaly': row['is_anomaly'] == true,
    'baseline': row['baseline'],
    'history_points': row['history_points'] | int,
    'history_days': row['history_days'] | int,
  }) %}
  {{ return(result) }}
{% endmacro %}


{#- MAD multiplier per sensitivity. -#}
{% macro _native_mad_multiplier(sensitivity) %}
  {{ return({'low': 4, 'medium': 3, 'high': 2}[sensitivity]) }}
{% endmacro %}


{% macro native__score_sql(series_sql, current_value, sensitivity, min_consecutive, observed_at=none) %}
  {% set k = icecore._native_mad_multiplier(sensitivity) %}
  {% set previous = (min_consecutive | int) - 1 %}
  {% set min_points = 7 %}
  {% set seasonal_min_days = 28 %}
  {% set bucket_min_points = 4 %}
  {% set cfg = var('icecore', {}) %}
  {% set floor_pct = icecore._sql_number(cfg.get('anomaly_mad_floor_pct', 0.01)) %}
  {% set floor_abs = icecore._sql_number(cfg.get('anomaly_mad_floor_abs', 0)) %}
  {% set sql %}
with series as (
    select observed_at::timestamp_ntz as observed_at, value::float as value,
           dayofweekiso(observed_at::timestamp_ntz) * 100 + hour(observed_at::timestamp_ntz) as bucket
    from ({{ series_sql }}) as s
    where value is not null and observed_at is not null
),
cur as (
    select {{ icecore._sql_number(current_value) }}::float as value,
           dayofweekiso(t) * 100 + hour(t) as bucket
    from (select {{ icecore._sql_timestamp(observed_at) }} as t)
),
span as (
    select count(*) as history_points,
           coalesce(datediff('day', min(observed_at)::date, max(observed_at)::date) + 1, 0) as history_days,
           coalesce(datediff('second', min(observed_at), max(observed_at)), 0) > {{ seasonal_min_days }} * 86400 as long_enough,
           count_if(series.bucket = cur.bucket) as bucket_points
    from series cross join cur
),
mode as (
    select history_points, history_days,
           case when history_points < {{ min_points }} then 'insufficient_history'
                when long_enough and bucket_points >= {{ bucket_min_points }} then 'seasonal'
                else 'flat' end as baseline
    from span
),
grouped as (
    select series.observed_at, series.value,
           iff(mode.baseline = 'seasonal', series.bucket, 0) as grp
    from series cross join mode
),
centers as (
    select grp, median(value) as expected from grouped group by grp
),
spreads as (
    select grouped.grp, centers.expected,
           greatest(median(abs(grouped.value - centers.expected)),
                    {{ floor_pct }} * abs(centers.expected),
                    {{ floor_abs }}) as mad
    from grouped join centers on grouped.grp = centers.grp
    group by grouped.grp, centers.expected
),
bands as (
    select grp, expected, expected - {{ k }} * mad as lower, expected + {{ k }} * mad as upper
    from spreads
),
recent as (
    select case when grouped.value > bands.upper then 1
                when grouped.value < bands.lower then -1
                else 0 end as direction
    from grouped join bands on grouped.grp = bands.grp
    qualify row_number() over (order by grouped.observed_at desc) <= {{ previous }}
),
streak as (
    select coalesce(count_if(direction = 1), 0) as above_points,
           coalesce(count_if(direction = -1), 0) as below_points
    from recent
),
scored as (
    select mode.baseline, mode.history_points, mode.history_days,
           bands.expected, bands.lower, bands.upper,
           case when mode.baseline = 'insufficient_history' or cur.value is null then 0
                when cur.value > bands.upper then cur.value - bands.upper
                when cur.value < bands.lower then cur.value - bands.lower
                else 0 end as score,
           streak.above_points, streak.below_points
    from cur
    cross join mode
    cross join streak
    left join bands on bands.grp = iff(mode.baseline = 'seasonal', cur.bucket, 0)
)
select 'native' as engine, expected, lower, upper, score,
       score <> 0 and iff(score > 0, above_points, below_points) >= {{ previous }} as is_anomaly,
       baseline, history_points, history_days
from scored
  {% endset %}
  {{ return(sql) }}
{% endmacro %}


{#- A timestamp as a SQL literal (escaped); none means now. -#}
{% macro _sql_timestamp(value) %}
  {% if value is none %}
    {{ return("convert_timezone('UTC', current_timestamp())::timestamp_ntz") }}
  {% endif %}
  {{ return("'" ~ (value | string).replace('\\', '\\\\').replace("'", "''") ~ "'::timestamp_ntz") }}
{% endmacro %}


{#- A number as a SQL literal; anything else is refused rather than pasted into SQL. -#}
{% macro _sql_number(value) %}
  {% if value is none %}
    {{ return('null') }}
  {% elif value is number %}
    {{ return(value | string) }}
  {% else %}
    {{ exceptions.raise_compiler_error("icecore: expected a number, got '" ~ value ~ "'") }}
  {% endif %}
{% endmacro %}
