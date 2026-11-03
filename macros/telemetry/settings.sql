{#- UC1 settings read by the hooks: one accessor for var('icecore'), with the defaults of the package's
    dbt_project.yml. The hooks render in the root project, where the package's own defaults are not visible,
    and `--vars '{icecore: {...}}'` replaces the whole dict, so every key has its default here. -#}

{% macro _icecore_setting(key, default=none) %}
  {%- set config = var('icecore', {}) -%}
  {%- set config = config if config is mapping else {} -%}
  {{ return(config.get(key, default)) }}
{% endmacro %}


{#- A boolean setting: booleans and numbers as Python reads them, strings true/1/yes/on (any case) as true. -#}
{% macro _icecore_flag(key, default=false) %}
  {%- set value = icecore._icecore_setting(key, default) -%}
  {%- if value is string -%}
    {{ return(value | trim | lower in ['true', '1', 'yes', 'on']) }}
  {%- endif -%}
  {{ return(true if value else false) }}
{% endmacro %}


{#- var('icecore').telemetry, default true: the hooks write nothing when it is false. -#}
{% macro _icecore_telemetry_enabled() %}
  {{ return(icecore._icecore_flag('telemetry', true)) }}
{% endmacro %}
