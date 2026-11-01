{#- UC1: the Snowflake query ids behind one run result, from result.adapter_response.query_id (a string, or a
    list where an adapter reports several). Empty values are dropped, duplicates kept once, order kept. dbt's
    adapter response holds the id of the node's main statement, so most nodes have one. -#}
{% macro collect_query_ids(result) %}
  {%- set response = icecore._icecore_get(result, 'adapter_response') -%}
  {%- set raw = icecore._icecore_get(response, 'query_id') if response is not none else none -%}
  {%- set raw = [] if raw is none else ([raw] if raw is string else raw) -%}
  {%- set ids = [] -%}
  {%- for value in raw -%}
    {%- if value is not none and (value | string | trim) and (value | string | trim) not in ids -%}
      {%- do ids.append(value | string | trim) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(ids) }}
{% endmacro %}
