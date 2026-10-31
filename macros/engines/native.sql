{#-
  Native engine (spec section 6): pure SQL, always available.
  This first version is the flat-window baseline only (median and MAD over the whole series);
  seasonal buckets and min_consecutive come with the native engine story.
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
    'history_points': row['history_points'] | int,
  }) %}
  {{ return(result) }}
{% endmacro %}


{#- MAD multiplier per sensitivity. -#}
{% macro _native_mad_multiplier(sensitivity) %}
  {{ return({'low': 4, 'medium': 3, 'high': 2}[sensitivity]) }}
{% endmacro %}


{% macro native__score_sql(series_sql, current_value, sensitivity, min_consecutive, observed_at=none) %}
  {% set k = icecore._native_mad_multiplier(sensitivity) %}
  {% set sql %}
with series as (
    select value::float as value
    from ({{ series_sql }}) as s
    where value is not null
),
center as (
    select median(value) as expected, count(*) as history_points from series
),
spread as (
    select median(abs(series.value - center.expected)) as mad
    from series cross join center
),
band as (
    select center.expected, center.history_points,
           center.expected - {{ k }} * spread.mad as lower,
           center.expected + {{ k }} * spread.mad as upper,
           {{ icecore._sql_number(current_value) }}::float as current_value
    from center cross join spread
)
select expected, lower, upper,
       case when current_value > upper then current_value - upper
            when current_value < lower then current_value - lower
            else 0 end as score,
       coalesce(score <> 0, false) as is_anomaly,
       history_points
from band
  {% endset %}
  {{ return(sql) }}
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
