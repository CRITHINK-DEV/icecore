{#-
  Shadow scoring (spec section 6): `shadow_engine` (var icecore.shadow_engine: none, native or cortex) scores the same
  series next to the primary engine, so a team can compare engines on its own data before switching. The primary
  verdict is never changed: shadow_score only reads the primary result, and the shadow fields travel under their own
  prefix, dbt.anomaly.shadow.*, in an `icecore.shadow` span event written through icecore.emit.

    {%- set result = icecore.score(history_sql, value, model=model, series=series) -%}
    {%- do icecore.emit_metric(relation, 'row_count', value, anomaly=result) -%}
    {%- set shadow = icecore.shadow_score(history_sql, value, result, model=model, series=series) -%}
    {%- do icecore.emit_shadow(relation, 'row_count', value, shadow, result) -%}

  Costs nothing when shadow is off or Cortex is off: no query runs.
-#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns the shadow engine's fields {engine, expected, lower, upper, score, is_anomaly, fallback_reason}, or none when
    there is nothing to compare: shadow disabled, the shadow engine is the one that scored, or Cortex is not available
    (then no Cortex call is made and one warning is logged). A Cortex shadow that cannot score (no or stale model, short
    history, error) returns engine cortex with null bands and the fallback_reason, so the gap is visible, and never
    falls back to native (native against native compares nothing). `primary` is the dispatcher's result; it is only read. -#}
{% macro shadow_score(series_sql, current_value, primary, sensitivity=none, min_consecutive=none, observed_at=none, model=none, series=none, shadow_engine=none, capabilities=none) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set shadow = icecore._shadow_engine(shadow_engine) -%}
  {%- if shadow == 'none' or shadow == (primary or {}).get('engine') -%}{{ return(none) }}{%- endif -%}
  {%- set settings = icecore._anomaly_settings(shadow, sensitivity, min_consecutive) -%}
  {%- if shadow == 'native' -%}
    {%- set scored = icecore.native__score(series_sql, current_value, settings.sensitivity, settings.min_consecutive, observed_at) -%}
    {{ return(icecore._shadow_fields(scored, none)) }}
  {%- endif -%}
  {%- set unavailable = icecore._cortex_unavailable_reason(capabilities) -%}
  {%- if unavailable is not none -%}
    {% do log('icecore: warning: shadow_engine is cortex but Cortex is not available (' ~ unavailable ~ '): no shadow scoring', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- if icecore._history_days(series_sql) < 14 -%}
    {{ return(icecore._shadow_fields(none, 'history_too_short')) }}
  {%- endif -%}
  {%- set attempt = icecore._cortex_attempt(model, series, series_sql, current_value, settings, observed_at) -%}
  {{ return(icecore._shadow_fields(attempt.get('result'), attempt.get('fallback_reason'))) }}
{% endmacro %}


{#- Writes the shadow result as one span event `icecore.shadow` through icecore.emit: dbt.metric.*, dbt.node.relation and
    dbt.anomaly.shadow.* (engine, expected, lower, upper, score, is_anomaly, fallback_reason, agrees with the primary
    verdict, primary_engine). Writes nothing and returns none when shadow is none (disabled). -#}
{% macro emit_shadow(relation, metric, value, shadow, primary=none, column=none, dimension=none) %}
  {%- if shadow is none -%}{{ return(none) }}{%- endif -%}
  {%- set attrs = icecore._shadow_attributes(relation, metric, value, shadow, primary, column, dimension) -%}
  {{ return(icecore.emit('shadow', 'icecore: shadow ' ~ metric ~ ' scored by ' ~ shadow.get('engine'), attrs)) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- var icecore.shadow_engine: none (default), native or cortex; anything else is refused. -#}
{% macro _shadow_engine(shadow_engine=none) %}
  {%- set shadow = var('icecore', {}).get('shadow_engine', 'none') if shadow_engine is none else shadow_engine -%}
  {%- set shadow = 'none' if shadow is none else shadow | string | lower -%}
  {%- if shadow not in ['none', 'native', 'cortex'] -%}
    {{ exceptions.raise_compiler_error("icecore: unknown shadow_engine '" ~ shadow ~ "' (expected none, native or cortex)") }}
  {%- endif -%}
  {{ return(shadow) }}
{% endmacro %}


{#- The six contract fields plus fallback_reason, from an engine result (or none with the reason it could not score). -#}
{% macro _shadow_fields(scored, fallback_reason) %}
  {%- if scored is none -%}
    {{ return({'engine': 'cortex', 'expected': none, 'lower': none, 'upper': none, 'score': none, 'is_anomaly': none, 'fallback_reason': fallback_reason}) }}
  {%- endif -%}
  {%- set out = {} -%}
  {%- for key in ['engine', 'expected', 'lower', 'upper', 'score', 'is_anomaly'] -%}
    {%- do out.update({key: scored.get(key)}) -%}
  {%- endfor -%}
  {%- do out.update({'fallback_reason': fallback_reason}) -%}
  {{ return(out) }}
{% endmacro %}


{#- Attributes of the icecore.shadow event; none-valued keys are left out. -#}
{% macro _shadow_attributes(relation, metric, value, shadow, primary=none, column=none, dimension=none) %}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set attrs = {'dbt.metric.name': metric | string, 'dbt.node.relation': name} -%}
  {%- do icecore._icecore_put(attrs, 'dbt.metric.value', icecore._icecore_metric_number(value)) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.metric.column', none if column is none else column | string) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.metric.dimension', none if dimension is none else dimension | string) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.anomaly.shadow.engine', none if shadow.get('engine') is none else shadow.get('engine') | string) -%}
  {%- for key in ['expected', 'lower', 'upper', 'score'] -%}
    {%- do icecore._icecore_put(attrs, 'dbt.anomaly.shadow.' ~ key, icecore._icecore_metric_number(shadow.get(key))) -%}
  {%- endfor -%}
  {%- set flag = shadow.get('is_anomaly') -%}
  {%- do icecore._icecore_put(attrs, 'dbt.anomaly.shadow.is_anomaly', flag if flag is boolean else none) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.anomaly.shadow.fallback_reason', shadow.get('fallback_reason')) -%}
  {%- if primary is not none -%}
    {%- do icecore._icecore_put(attrs, 'dbt.anomaly.shadow.primary_engine', primary.get('engine')) -%}
    {%- if flag is boolean and primary.get('is_anomaly') is boolean -%}
      {%- do attrs.update({'dbt.anomaly.shadow.agrees': flag == primary.get('is_anomaly')}) -%}
    {%- endif -%}
  {%- endif -%}
  {{ return(attrs) }}
{% endmacro %}
