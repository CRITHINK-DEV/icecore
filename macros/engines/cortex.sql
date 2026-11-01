{#-
  Cortex engine (spec section 6): scores one series with a SNOWFLAKE.ML.ANOMALY_DETECTION model trained per table on
  all its series. Models are refreshed by a task, never trained here. Scoring is one DETECT_ANOMALIES call against the
  existing model, inside a Snowflake Scripting block that catches every error, so a missing model, privilege or region
  never fails a test (as _icecore_try_scalar does).

  prediction_interval: 0.99 (low), 0.95 (medium), 0.90 (high). Cortex returns forecast, lower and upper bounds and
  its own is_anomaly; they are mapped to the contract: expected, lower, upper, score (signed distance outside the
  band, 0 inside, as native), is_anomaly. min_consecutive: the min_consecutive - 1 latest history points are scored
  in the same call and must be outside the band on the same side as the current value.
  Returns the contract fields with engine 'cortex', or {'error': '<code>: <message>'} when the model could not score.
-#}

{% macro cortex__score(model, series, series_sql, current_value, sensitivity, min_consecutive, observed_at=none) %}
  {% if not execute %}{{ return(none) }}{% endif %}
  {% set out = icecore._icecore_try_cortex(icecore.cortex__detect_sql(model, series, series_sql, current_value, sensitivity, min_consecutive, observed_at)) %}
  {% if 'error' in out %}
    {% do log('icecore: warning: Cortex model ' ~ model ~ ' could not score: ' ~ out['error'], info=True) %}
    {{ return({'error': out['error']}) }}
  {% endif %}
  {{ return(icecore._cortex_result(out['rows'], min_consecutive)) }}
{% endmacro %}


{#- The model name is pasted into SQL: only dotted plain or double-quoted identifiers are accepted. -#}
{% macro _cortex_check_model_name(model) %}
  {% if model is not string or not modules.re.match('^([A-Za-z_][A-Za-z0-9_$]*|"[^"]+")(\\.([A-Za-z_][A-Za-z0-9_$]*|"[^"]+")){0,2}$', model) %}
    {{ exceptions.raise_compiler_error("icecore: Cortex model must be a (qualified) identifier, got '" ~ model ~ "'") }}
  {% endif %}
{% endmacro %}


{#- Prediction interval per sensitivity. -#}
{% macro _cortex_prediction_interval(sensitivity) %}
  {{ return({'low': 0.99, 'medium': 0.95, 'high': 0.90}[sensitivity]) }}
{% endmacro %}


{#- The Scripting block text (no run): CALL <model>!DETECT_ANOMALIES over the latest history points and the current one. -#}
{% macro cortex__detect_sql(model, series, series_sql, current_value, sensitivity, min_consecutive, observed_at=none) %}
  {% do icecore._cortex_check_model_name(model) %}
  {% set previous = (min_consecutive | int) - 1 %}
  {% set interval = icecore._cortex_prediction_interval(sensitivity) %}
  {% set input_sql %}
select {{ icecore._icecore_sql_literal(series) }} as series, observed_at::timestamp_ntz as ts, value::float as y
from (select * from ({{ series_sql }}) where value is not null and observed_at is not null
      qualify row_number() over (order by observed_at desc) <= {{ previous }})
union all
select {{ icecore._icecore_sql_literal(series) }}, {{ icecore._sql_timestamp(observed_at) }}, {{ icecore._sql_number(current_value) }}::float
  {% endset %}
  {% set block %}
declare
  res variant;
begin
  call {{ model }}!detect_anomalies(
    input_data => system$query_reference({{ icecore._icecore_sql_literal(input_sql) }}),
    series_colname => 'SERIES', timestamp_colname => 'TS', target_colname => 'Y',
    config_object => {'prediction_interval': {{ interval }}});
  select coalesce(array_agg(object_construct(*)) within group (order by ts desc), array_construct()) into :res from table(result_scan(last_query_id()));
  return object_construct('rows', res);
exception
  when other then return object_construct('error', sqlcode || ': ' || sqlerrm);
end;
  {% endset %}
  {{ return(block) }}
{% endmacro %}


{#- Run a Scripting block that returns an object: {'rows': [...]} or {'error': ...}; never raises. -#}
{% macro _icecore_try_cortex(block) %}
  {{ return(fromjson(run_query('execute immediate ' ~ icecore._icecore_sql_literal(block)).rows[0][0])) }}
{% endmacro %}


{#- Map DETECT_ANOMALIES rows (latest first: the current value, then the history points) to the contract. -#}
{% macro _cortex_result(rows, min_consecutive) %}
  {% if not rows %}
    {{ return({'error': 'the model returned no row for the series'}) }}
  {% endif %}
  {% set cur = rows[0] %}
  {% set directions = [] %}
  {% for row in rows %}
    {% do directions.append(1 if row['Y'] > row['UPPER_BOUND'] else (-1 if row['Y'] < row['LOWER_BOUND'] else 0)) %}
  {% endfor %}
  {% set side = directions[0] %}
  {% set previous = (min_consecutive | int) - 1 %}
  {% set streak = directions[1:] | select('equalto', side) | list | length %}
  {% set score = 0 if side == 0 else (cur['Y'] - cur['UPPER_BOUND'] if side == 1 else cur['Y'] - cur['LOWER_BOUND']) %}
  {{ return({
    'engine': 'cortex',
    'expected': cur['FORECAST'] | float,
    'lower': cur['LOWER_BOUND'] | float,
    'upper': cur['UPPER_BOUND'] | float,
    'score': score | float,
    'is_anomaly': side != 0 and streak >= previous and (cur['IS_ANOMALY'] is sameas true),
  }) }}
{% endmacro %}
