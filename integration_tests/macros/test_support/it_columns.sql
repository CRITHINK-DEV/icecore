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


{#- Change window and dimension metrics, as a monitor would chain them. -#}
{% macro it_dimension_metrics(rel, dimension_column, since=none, timestamp_column=none, max_values=none) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {%- set window = icecore.get_change_window(relation, since, timestamp_column) -%}
  {% do log('icecore_it_result:' ~ tojson({'window': window,
      'dimensions': icecore.compute_dimension_metrics(relation, dimension_column, window, max_values)}), info=True) %}
{% endmacro %}

{#- Rendering only (no query). -#}
{% macro it_dimension_metrics_sql(rel, dimension_column, window, max_values) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_dimension_metrics_sql(relation, dimension_column, window, max_values)), info=True) %}
{% endmacro %}


{#- compute_* straight from a given window (no probe): input checks and window switching. -#}
{% macro it_compute_column_metrics(rel, column_name, window) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {% do log('icecore_it_result:' ~ tojson(icecore.compute_column_metrics(relation, column_name, window)), info=True) %}
{% endmacro %}

{% macro it_compute_dimension_metrics(rel, dimension_column, window) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {% do log('icecore_it_result:' ~ tojson(icecore.compute_dimension_metrics(relation, dimension_column, window)), info=True) %}
{% endmacro %}

{#- One window, computed `times` times: the fallback must happen once and the window must remember it. -#}
{% macro it_column_metrics_repeat(rel, column_name, since, timestamp_column, times) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {%- set window = icecore.get_change_window(relation, since, timestamp_column) -%}
  {%- set results = [] -%}
  {%- for i in range(times) -%}{%- do results.append(icecore.compute_column_metrics(relation, column_name, window)) -%}{%- endfor -%}
  {% do log('icecore_it_result:' ~ tojson({'window': window, 'metrics': results}), info=True) %}
{% endmacro %}
