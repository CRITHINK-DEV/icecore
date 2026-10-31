{#- Test wrappers for the UC2 metadata readers. Results are logged as `icecore_it_result:<json>` for pytest. -#}

{% macro it_relation(database, schema, identifier, quoted=false) %}
  {{ return(api.Relation.create(
      database=database, schema=schema, identifier=identifier,
      quote_policy={'database': false, 'schema': false, 'identifier': quoted}
  )) }}
{% endmacro %}

{% macro it_quoted_name(database, schema, identifier, quoted=false) %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_quoted_name(it_relation(database, schema, identifier, quoted))), info=True) %}
{% endmacro %}

{#- relations: list of {database, schema, identifier, quoted}; result keyed by identifier. -#}
{% macro it_metadata(relations, since=none) %}
  {%- set out = {} -%}
  {%- for r in relations -%}
    {%- set relation = it_relation(r['database'], r['schema'], r['identifier'], r.get('quoted', false)) -%}
    {%- do out.update({r['identifier']: {
        'row_count': icecore.get_row_count_metadata(relation),
        'rows_added': none if since is none else icecore.get_row_count_changes(relation, since),
        'last_change': icecore.get_last_change_time(relation),
    }}) -%}
  {%- endfor -%}
  {% do log('icecore_it_result:' ~ tojson(out), info=True) %}
{% endmacro %}
