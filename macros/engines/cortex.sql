{#-
  Cortex engine (spec section 6): scores one series with a SNOWFLAKE.ML.ANOMALY_DETECTION model trained per table on
  all its series. Models are refreshed by a task, never trained here. Scoring is one DETECT_ANOMALIES call against the
  existing model, made by the persisted owner's-rights procedure ICECORE_SCORE (macros/setup/procedures.sql) inside a
  Snowflake Scripting block that catches every error, so a missing model, privilege or region never fails a test (as
  _icecore_try_scalar does). Models cannot be shared across roles (Snowflake's documented limitation), so they are
  trained and scored in setup's role: the dbt role needs only USAGE on ICECORE_SCORE. The procedure's arguments are
  bound values (the model name ICECORE_M_<16 hex>, a series key, an array of points, numbers), never SQL text. The procedure has no database or schema argument: it reads the models of its own obs schema only.

  prediction_interval: 0.99 (low), 0.95 (medium), 0.90 (high). The sensitivities are not the same width across engines:
  Cortex high (0.90) is wider than native high (k = 2 MADs, about an 82% band on normal data), so the same sensitivity
  flags somewhat fewer points with Cortex. Cortex returns forecast, lower and upper bounds and
  its own is_anomaly; they are mapped to the contract: expected, lower, upper, score (signed distance outside the
  band, 0 inside, as native), is_anomaly. min_consecutive: the min_consecutive - 1 latest history points are scored
  in the same call and must be outside the band on the same side as the current value.
  Returns the contract fields with engine 'cortex', or {'error': message, 'state': 'error'|'missing'|'stale', 'age_days'}
  when the model could not score (missing: no such model for setup's role; stale: older than refresh_days).
-#}

{% macro cortex__score(model, series, series_sql, current_value, sensitivity, min_consecutive, observed_at=none, schema=none, refresh_days=none) %}
  {% if not execute %}{{ return(none) }}{% endif %}
  {% if not icecore._cortex_model_ok(model) %}
    {{ return({'error': 'the Cortex model must be named ICECORE_M_ and 16 upper-case hex digits (cortex_model_name), got ' ~ model, 'state': 'error', 'age_days': none}) }}
  {% endif %}
  {% set call = icecore._cortex_call_sql(model, series, current_value, sensitivity, observed_at, schema, refresh_days,
                                        icecore._cortex_previous_points(series_sql, min_consecutive)) %}
  {% set out = icecore._icecore_try_cortex(call) %}
  {% if 'error' in out %}
    {% do log('icecore: warning: Cortex model ' ~ model ~ ' could not score: ' ~ out['error'], info=True) %}
    {{ return({'error': out['error'], 'state': 'error', 'age_days': none}) }}
  {% endif %}
  {% if out.get('state') != 'ok' %}
    {{ return({'error': 'model ' ~ model ~ ' is ' ~ out.get('state'), 'state': out.get('state'), 'age_days': out.get('age_days')}) }}
  {% endif %}
  {{ return(icecore._cortex_result(out['rows'], min_consecutive)) }}
{% endmacro %}


{#- A model name ICECORE_SCORE accepts: ICECORE_M_ and 16 upper-case hex digits (see cortex_model_name). The procedure checks it
    again before any use; here a bad name is an error value (a fallback), never a failed test. -#}
{% macro _cortex_model_ok(model) %}
  {{ return(model is string and modules.re.match('^ICECORE_M_[0-9A-F]{16}\\Z', model) is not none) }}
{% endmacro %}


{#- Prediction interval per sensitivity. -#}
{% macro _cortex_prediction_interval(sensitivity) %}
  {{ return({'low': 0.99, 'medium': 0.95, 'high': 0.90}[sensitivity]) }}
{% endmacro %}


{#- cortex_refresh_days: a number of days, 0 or more (default 7, the refresh task's period). -#}
{% macro _cortex_refresh_days(refresh_days=none) %}
  {% set refresh_days = var('icecore', {}).get('cortex_refresh_days', 7) if refresh_days is none else refresh_days %}
  {% if refresh_days is not number or refresh_days is sameas true or refresh_days is sameas false or refresh_days < 0 %}
    {{ exceptions.raise_compiler_error("icecore: cortex_refresh_days must be a number of days, 0 or more, got '" ~ refresh_days ~ "'") }}
  {% endif %}
  {{ return(refresh_days) }}
{% endmacro %}


{#- The min_consecutive - 1 latest history points as [{ts, y}], latest first; [] when min_consecutive is 1 (no query). -#}
{% macro _cortex_previous_points(series_sql, min_consecutive) %}
  {% set previous = (min_consecutive | int) - 1 %}
  {% if previous < 1 %}{{ return([]) }}{% endif %}
  {% set got = icecore._icecore_try_scalar("select array_agg(object_construct('ts', to_varchar(observed_at::timestamp_ntz, 'YYYY-MM-DD HH24:MI:SS.FF3'), 'y', value::float)) from (select observed_at, value from (" ~ series_sql ~ ") where value is not null and observed_at is not null qualify row_number() over (order by observed_at desc) <= " ~ previous ~ ")") %}
  {{ return([] if 'error' in got else (got.get('value') or [])) }}
{% endmacro %}


{#- The Scripting block text (no run): CALL ICECORE_SCORE with the current point and the previous ones. Every value is a
    typed argument or goes through the shared icecore._sql. The series key is hashed inside the procedure. -#}
{% macro _cortex_call_sql(model, series, current_value, sensitivity, observed_at, schema, refresh_days, previous) %}
  {% set interval = icecore._cortex_prediction_interval(sensitivity) %}
  {% set points = [{'ts': none if observed_at is none else observed_at | string, 'y': icecore._sql_number(current_value) | float}] + previous %}
  {% set payload = {'model': model, 'series': series, 'points': points} %}
  {% set block %}
declare
  res variant;
  payload variant default parse_json({{ icecore._sql(tojson(payload), 'string') }});
begin
  call {{ icecore._icecore_procedure('ICECORE_SCORE', schema) }}(:payload:model::string, :payload:series::string,
    to_array(:payload:points), {{ interval }}, {{ icecore._cortex_refresh_days(refresh_days) }}) into :res;
  return res;
exception
  when other then return object_construct('error', sqlcode || ': ' || sqlerrm);
end;
  {% endset %}
  {{ return(block) }}
{% endmacro %}


{#- Run a Scripting block that returns an object; never raises. -#}
{% macro _icecore_try_cortex(block) %}
  {{ return(fromjson(run_query('execute immediate ' ~ icecore._sql(block, 'string')).rows[0][0])) }}
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
