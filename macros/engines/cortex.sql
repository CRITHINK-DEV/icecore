{#-
  Cortex engine (spec section 6): scores one series with a SNOWFLAKE.ML.ANOMALY_DETECTION model trained per table on
  all its series. Models are refreshed by a task, never trained here. Scoring is one DETECT_ANOMALIES call against the
  existing model, inside a Snowflake Scripting block that catches every error, so a missing model, privilege or region
  never fails a test (as _icecore_try_scalar does).

  prediction_interval: 0.99 (low), 0.95 (medium), 0.90 (high). The sensitivities are not the same width across engines:
  Cortex high (0.90) is wider than native high (k = 2 MADs, about an 82% band on normal data), so the same sensitivity
  flags somewhat fewer points with Cortex. Cortex returns forecast, lower and upper bounds and
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
  {% if cur['Y'] is none or cur['LOWER_BOUND'] is none or cur['UPPER_BOUND'] is none or cur['FORECAST'] is none %}
    {{ return({'error': 'the model returned no band for the series (null bound)'}) }}
  {% endif %}
  {% set directions = [] %}
  {% for row in rows %}
    {% if row['Y'] is none or row['UPPER_BOUND'] is none or row['LOWER_BOUND'] is none %}
      {% do directions.append(0) %}
    {% else %}
      {% do directions.append(1 if row['Y'] > row['UPPER_BOUND'] else (-1 if row['Y'] < row['LOWER_BOUND'] else 0)) %}
    {% endif %}
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


{#-
  Fallbacks. A model that is missing, older than the refresh period, or unreadable never fails a test: the
  dispatcher scores with native and records why (fallback_reason).
-#}

{#- 'fresh', 'missing', 'stale' or 'error' for the per-table model, read with SHOW SNOWFLAKE.ML.ANOMALY_DETECTION inside a
    catching block. Stale: created more than var icecore.cortex_refresh_days (default 7) days ago, the period of the
    task that refreshes the models. Returns {'state', 'age_days', 'error'}; never raises. -#}
{% macro _cortex_model_state(model, refresh_days=none) %}
  {% do icecore._cortex_check_model_name(model) %}
  {% set refresh_days = var('icecore', {}).get('cortex_refresh_days', 7) if refresh_days is none else refresh_days %}
  {% if refresh_days is not number or refresh_days is sameas true or refresh_days is sameas false or refresh_days < 0 %}
    {{ exceptions.raise_compiler_error("icecore: cortex_refresh_days must be a number of days, 0 or more, got '" ~ refresh_days ~ "'") }}
  {% endif %}
  {% set parts = [] %}
  {#- split on dots outside double quotes: a quoted name may contain dots -#}
  {% for part in modules.re.findall('"[^"]+"|[^.]+', model) %}
    {% do parts.append(part[1:-1] if part.startswith('"') else part | upper) %}
  {% endfor %}
  {% set name = parts[-1] %}
  {% set schema = parts[-2] if parts | length > 1 else target.schema | upper %}
  {% set database = parts[-3] if parts | length > 2 else target.database | upper %}
  {% set block %}
declare
  res variant;
begin
  show snowflake.ml.anomaly_detection like {{ icecore._icecore_sql_literal(name) }} in schema "{{ database | replace('"', '""') }}"."{{ schema | replace('"', '""') }}";
  select coalesce(array_agg(object_construct('age_s', datediff('second', "created_on", current_timestamp()))), array_construct()) into :res from table(result_scan(last_query_id()));
  return object_construct('rows', res);
exception
  when other then return object_construct('error', sqlcode || ': ' || sqlerrm);
end;
  {% endset %}
  {% set out = icecore._icecore_try_cortex(block) %}
  {% if 'error' in out %}
    {{ return({'state': 'error', 'age_days': none, 'error': out['error']}) }}
  {% endif %}
  {{ return(icecore._cortex_state_of(out['rows'], refresh_days)) }}
{% endmacro %}


{#- Pure: the model state from the SHOW rows (age in seconds). -#}
{% macro _cortex_state_of(rows, refresh_days) %}
  {% if not rows %}
    {{ return({'state': 'missing', 'age_days': none, 'error': none}) }}
  {% endif %}
  {% set age_days = rows[0]['age_s'] / 86400 %}
  {{ return({'state': 'stale' if age_days > refresh_days else 'fresh', 'age_days': age_days, 'error': none}) }}
{% endmacro %}
