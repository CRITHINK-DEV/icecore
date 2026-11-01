{#- Test wrappers for compute_columns_metrics (macros/monitors/columns.sql). `rel` as for it_columns.sql. -#}

{#- Rendering only (no query): the SQL of the one multi-column query. -#}
{% macro it_columns_metrics_sql(rel, columns, window, metrics=none) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {%- set names = icecore._icecore_column_metric_names(metrics) -%}
  {%- set spec = [] -%}
  {%- for c in columns -%}{%- do spec.append({'column': icecore._icecore_column_name(c), 'metrics': names}) -%}{%- endfor -%}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_columns_metrics_sql(relation, spec, window)), info=True) %}
{% endmacro %}

{#- Both paths over one window: the multi-column result and the per-column results, to compare. -#}
{% macro it_columns_equivalence(rel, columns, since, timestamp_column=none, metrics=none) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {%- set window = icecore.get_change_window(relation, since, timestamp_column) -%}
  {%- set single = {} -%}
  {%- for c in columns -%}{%- do single.update({c: icecore.compute_column_metrics(relation, c, window, metrics)}) -%}{%- endfor -%}
  {% do log('icecore_it_result:' ~ tojson({'window': window, 'single': single,
      'multi': icecore.compute_columns_metrics(relation, columns, window, metrics)}), info=True) %}
{% endmacro %}

{#- The multi-column path first, `times` times on one window (it must do the fallback, once), then the per-column path
    on the switched window for comparison. -#}
{% macro it_columns_multi_first(rel, columns, since, timestamp_column, times) %}
  {%- set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) -%}
  {%- set window = icecore.get_change_window(relation, since, timestamp_column) -%}
  {%- set multi = [] -%}
  {%- for i in range(times) -%}{%- do multi.append(icecore.compute_columns_metrics(relation, columns, window)) -%}{%- endfor -%}
  {%- set single = {} -%}
  {%- for c in columns -%}{%- do single.update({c: icecore.compute_column_metrics(relation, c, window)}) -%}{%- endfor -%}
  {% do log('icecore_it_result:' ~ tojson({'window': window, 'multi': multi, 'single': single}), info=True) %}
{% endmacro %}
