{#- Test wrappers for the UC2 column monitors (macros/monitors/columns.sql).
    `rel` is {database, schema, identifier, quoted} as for it_metadata. Results are logged as
    `icecore_it_result:<json>` for pytest. -#}

{#- Change window and column metrics, as a monitor would chain them. -#}
{% macro it_column_metrics(rel, column_name, since=none, timestamp_column=none, metrics=none) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {%- set window = icecore.get_change_window(relation, since, timestamp_column) -%}
  {%- set out = {'window': window, 'metrics': icecore.compute_column_metrics(relation, column_name, window, metrics)} -%}
  {% do log('icecore_it_result:' ~ tojson(out), info=True) %}
{% endmacro %}

{#- Rendering only (no query): the SQL compute_column_metrics runs for a given window. -#}
{% macro it_column_metrics_sql(rel, column_name, window, metrics=none) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {% do log('icecore_it_result:' ~ tojson({
      'metrics_sql': icecore._icecore_column_metrics_sql(relation, column_name, window, metrics),
      'changes_source': icecore._icecore_changes_source(relation, window['since']),
      'row_delta_sql': icecore._icecore_row_delta_sql(relation, window['since']),
  }), info=True) %}
{% endmacro %}
