{#- icecore.volume: zero-scan volume test (UC2). Documented in macros/tests/volume.yml (dbt reads a generic test's docs only from macro-paths).
    Reads the total row count with icecore.get_row_count_metadata (INFORMATION_SCHEMA.TABLES, no data scan),
    emits it as the metric row_count, then compares it with min_rows and max_rows when given. The test fails
    or passes only on that comparison: emit_metric guards its own CALL. -#}
{% test volume(model, min_rows=none, max_rows=none) %}
  {%- for pair in [('min_rows', min_rows), ('max_rows', max_rows)] -%}
    {%- if pair[1] is not none and (pair[1] is boolean or pair[1] is not number or pair[1] < 0) -%}
      {{ exceptions.raise_compiler_error("icecore.volume: " ~ pair[0] ~ " must be a non-negative number; got '" ~ pair[1] ~ "'") }}
    {%- endif -%}
  {%- endfor -%}
  {%- if min_rows is not none and max_rows is not none and min_rows > max_rows -%}
    {{ exceptions.raise_compiler_error("icecore.volume: min_rows (" ~ min_rows ~ ") is above max_rows (" ~ max_rows ~ ")") }}
  {%- endif -%}

  {%- set breach = false -%}
  {%- set unmeasured = none -%}
  {%- set row_count = none -%}
  {%- if execute -%}
    {%- set row_count = icecore.get_row_count_metadata(model) -%}
    {%- if row_count is none -%}
      {#- A view, an external table, an Iceberg table, or not readable (the reader logged why): nothing to judge. -#}
      {%- do log('icecore: warning: volume of ' ~ model ~ ' could not be measured from metadata', info=True) -%}
      {#- No threshold: nothing to judge, the test passes. With min_rows or max_rows: one failing row, so the test's severity applies. -#}
      {%- if min_rows is not none or max_rows is not none -%}
        {%- set unmeasured = 'unmeasured: no trusted metadata row count (view, external or Iceberg table, missing, or not readable)' -%}
      {%- endif -%}
    {%- else -%}
      {%- set breach = (min_rows is not none and row_count < min_rows) or (max_rows is not none and row_count > max_rows) -%}
      {#- UC3 scoring hook: icecore.score(<history of row_count>, row_count) plugs in here and its result goes
          to emit_metric as `anomaly=`. Not called at this stage (engine none). -#}
      {%- do icecore.emit_metric(model, 'row_count', row_count) -%}
    {%- endif -%}
  {%- endif -%}

  select {{ 'null' if row_count is none else row_count }}::number as row_count,
         {{ 'null' if min_rows is none else min_rows }}::number as min_rows,
         {{ 'null' if max_rows is none else max_rows }}::number as max_rows,
         {{ 'null' if unmeasured is none else icecore._icecore_sql_literal(unmeasured) }}::varchar as reason
  where {{ 'true' if (breach or unmeasured is not none) else 'false' }}
{% endtest %}
