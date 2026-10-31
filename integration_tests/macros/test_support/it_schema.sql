{#- Test wrappers for the UC2 schema macros. Results are logged as `icecore_it_result:<json>` for pytest. -#}

{% macro it_diff_schema(previous, current) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.diff_schema(previous, current)), info=True) %}
{% endmacro %}

{% macro it_schema(database, schema, identifier, quoted=false, previous=none) %}
  {%- set relation = api.Relation.create(
      database=database, schema=schema, identifier=identifier,
      quote_policy={'database': false, 'schema': false, 'identifier': quoted}
  ) -%}
  {%- set signature = icecore.get_columns_signature(relation) -%}
  {%- set out = {'signature': signature} -%}
  {%- if previous is not none -%}
    {%- do out.update({'diff': icecore.diff_schema(previous, signature)}) -%}
  {%- endif -%}
  {% do log('icecore_it_result:' ~ tojson(out), info=True) %}
{% endmacro %}
