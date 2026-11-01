{#-
  Anomaly engine dispatcher (spec section 6). One contract whatever engine scores:
  engine, expected, lower, upper, score, is_anomaly, plus fallback_reason when the requested
  engine was not the one that scored. Computes and returns values only; emission is emit_metric's job.
-#}

{% macro score(series_sql, current_value, engine=none, sensitivity=none, min_consecutive=none, observed_at=none, model=none, series=none, capabilities=none) %}
  {% set settings = icecore._anomaly_settings(engine, sensitivity, min_consecutive) %}
  {#- At parse time nothing runs: return the contract shape with nulls so callers can render. -#}
  {% if not execute %}
    {{ return({'engine': none, 'expected': none, 'lower': none, 'upper': none, 'score': none,
               'is_anomaly': none, 'fallback_reason': none}) }}
  {% endif %}

  {#- native never considers Cortex. cortex and auto trust the anomaly_engine var (setup prints auto only where Cortex is
      available) and go straight to the guarded calls: no detection up front. -#}
  {% set choice = {'engine': 'native', 'fallback_reason': none} %}
  {% set result = none %}
  {% if settings.engine != 'native' %}
    {% set attempt = icecore._cortex_attempt(model, series, series_sql, current_value, settings, observed_at, capabilities) %}
    {% if attempt.result is not none %}
      {% set result = attempt.result %}
      {% do result.update({'fallback_reason': none}) %}
    {% else %}
      {% set choice = {'engine': 'native', 'fallback_reason': attempt.fallback_reason, 'fallback_detail': attempt.detail} %}
    {% endif %}
  {% endif %}

  {% if choice.fallback_reason is not none %}
    {{ log("icecore: anomaly engine '" ~ settings.engine ~ "' scored with native (" ~ choice.fallback_reason ~ ")") }}
  {% endif %}

  {% if result is none %}
    {% set result = icecore.native__score(series_sql, current_value, settings.sensitivity, settings.min_consecutive, observed_at) %}
    {% do result.update({'fallback_reason': choice.fallback_reason}) %}
    {% if choice.get('fallback_detail') is not none %}
      {% do result.update({'fallback_detail': choice.fallback_detail}) %}
    {% endif %}
  {% endif %}
  {{ return(result) }}
{% endmacro %}


{#- Resolve defaults from var('icecore') and reject unknown settings. -#}
{% macro _anomaly_settings(engine, sensitivity, min_consecutive) %}
  {% set cfg = var('icecore', {}) %}
  {% set engine = cfg.get('anomaly_engine', 'auto') if engine is none else engine %}
  {% set sensitivity = cfg.get('anomaly_sensitivity', 'medium') if sensitivity is none else sensitivity %}
  {% set min_consecutive = cfg.get('anomaly_min_consecutive', 1) if min_consecutive is none else min_consecutive %}
  {% if engine not in ['native', 'cortex', 'auto'] %}
    {{ exceptions.raise_compiler_error("icecore: unknown anomaly engine '" ~ engine ~ "' (expected native, cortex or auto)") }}
  {% endif %}
  {% if sensitivity not in ['low', 'medium', 'high'] %}
    {{ exceptions.raise_compiler_error("icecore: unknown anomaly sensitivity '" ~ sensitivity ~ "' (expected low, medium or high)") }}
  {% endif %}
  {% if min_consecutive is not integer or min_consecutive < 1 %}
    {{ exceptions.raise_compiler_error("icecore: min_consecutive must be an integer of at least 1, got '" ~ min_consecutive ~ "'") }}
  {% endif %}
  {{ return({'engine': engine, 'sensitivity': sensitivity, 'min_consecutive': min_consecutive}) }}
{% endmacro %}


{#-
  Selection rules, pure (no query):
    native -> native, Cortex never considered
    cortex -> cortex, else native with the reason
    auto   -> cortex if available and at least 14 days of history, else native with the reason
-#}
{% macro _select_engine(engine, cortex_unavailable_reason, history_days) %}
  {% if engine == 'native' %}
    {{ return({'engine': 'native', 'fallback_reason': none}) }}
  {% elif cortex_unavailable_reason is not none %}
    {{ return({'engine': 'native', 'fallback_reason': cortex_unavailable_reason}) }}
  {% elif history_days is none or history_days < 14 %}
    {{ return({'engine': 'native', 'fallback_reason': 'history_too_short'}) }}
  {% else %}
    {{ return({'engine': 'cortex', 'fallback_reason': none}) }}
  {% endif %}
{% endmacro %}


{#- None when Cortex can score, else why not, from a `detect_capabilities` result the caller already holds (its `cortex` and
    `cortex_reason`). Without one nothing is detected here: the anomaly_engine var decides (setup prints auto only where
    Cortex is available) and a failing guarded call is diagnosed afterwards by _cortex_diagnose. -#}
{% macro _cortex_unavailable_reason(capabilities=none) %}
  {% if capabilities is none or capabilities.get('cortex') is sameas true %}
    {{ return(none) }}
  {% endif %}
  {% do log('icecore: Cortex is unavailable: ' ~ capabilities.get('cortex_reason'), info=True) %}
  {{ return('cortex_unavailable') }}
{% endmacro %}


{#- After a failed guarded Cortex call: run detect_capabilities (without recording) to tell cortex_unavailable (the class is not
    visible to the role or region) from cortex_error (anything else). Returns {'fallback_reason', 'detail'}. -#}
{% macro _cortex_diagnose(error) %}
  {% set capabilities = icecore.detect_capabilities(record=false) %}
  {% if capabilities is not none and capabilities.get('cortex') is not sameas true %}
    {{ return({'fallback_reason': 'cortex_unavailable', 'detail': capabilities.get('cortex_reason')}) }}
  {% endif %}
  {{ return({'fallback_reason': 'cortex_error', 'detail': error}) }}
{% endmacro %}


{#- Try the Cortex engine for one series: {'result': contract dict} or {'result': none, 'fallback_reason', 'detail'}.
    Never raises. Order, cheapest first: capabilities given and Cortex off (no query), no model given (no query),
    history under 14 days (1 query), the model's state (1 guarded SHOW), the DETECT call (1 guarded). A failing guard is
    diagnosed with detect_capabilities. A working call costs 3 queries. -#}
{% macro _cortex_attempt(model, series, series_sql, current_value, settings, observed_at, capabilities=none) %}
  {% set unavailable = icecore._cortex_unavailable_reason(capabilities) %}
  {% if unavailable is not none %}
    {{ return({'result': none, 'fallback_reason': unavailable, 'detail': capabilities.get('cortex_reason')}) }}
  {% endif %}
  {% if model is none or series is none %}
    {{ return({'result': none, 'fallback_reason': 'model_missing', 'detail': 'no Cortex model was given for this series'}) }}
  {% endif %}
  {% set choice = icecore._select_engine('cortex', none, icecore._history_days(series_sql)) %}
  {% if choice.engine != 'cortex' %}
    {{ return({'result': none, 'fallback_reason': choice.fallback_reason, 'detail': 'fewer than 14 days of history'}) }}
  {% endif %}
  {% set state = icecore._cortex_model_state(model) %}
  {% if state.state == 'missing' %}
    {{ return({'result': none, 'fallback_reason': 'model_missing', 'detail': 'no model ' ~ model}) }}
  {% elif state.state == 'stale' %}
    {{ return({'result': none, 'fallback_reason': 'model_stale', 'detail': 'model ' ~ model ~ ' is ' ~ (state.age_days | round(1)) ~ ' days old'}) }}
  {% elif state.state == 'error' %}
    {% set d = icecore._cortex_diagnose(state.error) %}
    {{ return({'result': none, 'fallback_reason': d.fallback_reason, 'detail': d.detail}) }}
  {% endif %}
  {% set scored = icecore.cortex__score(model, series, series_sql, current_value, settings.sensitivity, settings.min_consecutive, observed_at) %}
  {% if 'error' in scored %}
    {% set d = icecore._cortex_diagnose(scored.error) %}
    {{ return({'result': none, 'fallback_reason': d.fallback_reason, 'detail': d.detail}) }}
  {% endif %}
  {{ return({'result': scored}) }}
{% endmacro %}


{#- Calendar days covered by the series, first to last observation inclusive. -#}
{% macro _history_days(series_sql) %}
  {% if not execute %}{{ return(none) }}{% endif %}
  {% set sql %}
    select coalesce(datediff('day', min(observed_at)::date, max(observed_at)::date) + 1, 0) as days
    from ({{ series_sql }}) as series
  {% endset %}
  {{ return(run_query(sql).rows[0][0] | int) }}
{% endmacro %}
