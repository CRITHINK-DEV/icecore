{#- Everything, Elementary's tables included, lands in the target schema ELEMENTARY_PROBE_<run_id>,
    so the probe writes into exactly one schema that the test drops at the end. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ target.schema }}
{%- endmacro %}
