{#- icecore.freshness: zero-scan freshness test (UC2). Documented in macros/tests/freshness.yml (dbt reads a generic test's docs only from macro-paths).
    Reads the last change time with icecore.get_last_change_time (SYSTEM$LAST_CHANGE_COMMIT_TIME, no data scan),
    takes the lag against Snowflake's clock, emits it as the metric freshness_lag_seconds, then compares it with
    max_lag. The test fails or passes only on that comparison: emit_metric guards its own CALL. -#}
{% test freshness(model, max_lag=none) %}
  {%- set max_lag_seconds = none -%}
  {%- if max_lag is not none -%}
    {%- if max_lag is boolean or (max_lag is number and max_lag < 0) -%}
      {{ exceptions.raise_compiler_error("icecore.freshness: max_lag must be a non-negative number of seconds or a number with a unit s, m, h or d; got '" ~ max_lag ~ "'") }}
    {%- elif max_lag is number -%}
      {%- set max_lag_seconds = max_lag -%}
    {%- else -%}
      {%- set parsed = modules.re.match('^\\s*(\\d+(?:\\.\\d+)?)\\s*([smhd])\\s*$', max_lag | string | lower) -%}
      {%- if parsed is none -%}
        {{ exceptions.raise_compiler_error("icecore.freshness: max_lag must be a number of seconds or a number with a unit s, m, h or d (for example 30m, 2h, 1d); got '" ~ max_lag ~ "'") }}
      {%- endif -%}
      {%- set max_lag_seconds = (parsed.group(1) | float) * ({'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[parsed.group(2)]) -%}
    {%- endif -%}
  {%- endif -%}

  {%- set breach = false -%}
  {%- set unmeasured = none -%}
  {%- set lag_seconds = none -%}
  {%- if execute -%}
    {%- set last_change = icecore.get_last_change_time(model) -%}
    {%- if last_change is none -%}
      {#- Never written, or not readable (the reader logged why): there is no lag to judge, so the test passes. -#}
      {%- do log('icecore: warning: freshness of ' ~ model ~ ' could not be measured: no last change time (never written, or not readable)', info=True) -%}
      {#- No threshold: nothing to judge, the test passes. With max_lag: one failing row, so the test's severity applies. -#}
      {%- if max_lag_seconds is not none -%}
        {%- set unmeasured = 'unmeasured: no last change time (never written, or not readable)' -%}
      {%- endif -%}
    {%- else -%}
      {%- set ms = run_query("select datediff('millisecond', to_timestamp_tz('" ~ last_change ~ "'), current_timestamp())").rows[0][0] -%}
      {%- set lag_seconds = ((ms | float) / 1000) | round(3) -%}
      {%- set breach = max_lag_seconds is not none and lag_seconds > max_lag_seconds -%}
      {#- UC3 scoring hook: icecore.score(<history of freshness_lag_seconds>, lag_seconds) plugs in here and its
          result goes to emit_metric as `anomaly=`. Not called at this stage (engine none). -#}
      {%- do icecore.emit_metric(model, 'freshness_lag_seconds', lag_seconds) -%}
    {%- endif -%}
  {%- endif -%}

  select {{ 'null' if lag_seconds is none else lag_seconds }}::float as lag_seconds,
         {{ 'null' if max_lag_seconds is none else max_lag_seconds }}::float as max_lag_seconds,
         {{ 'null' if unmeasured is none else icecore._icecore_sql_literal(unmeasured) }}::varchar as reason
  where {{ 'true' if (breach or unmeasured is not none) else 'false' }}
{% endtest %}
