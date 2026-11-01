{#- Test wrappers for UC8 compat views (macros/compat/). Results are logged as `icecore_it_result:<json>`. -#}

{#- The SELECT and the columns of elementary_test_results. -#}
{% macro it_compat_test_results() %}
  {% do _it_result({'sql': icecore.elementary_test_results_view_sql('"DB"."OBS"'),
                    'columns': icecore._icecore_elementary_test_results_columns()}) %}
{% endmacro %}

{#- The list of compat views, each {name, sql}. -#}
{% macro it_compat_views_render(schema=none) %}
  {% do _it_result(icecore._icecore_compat_views(schema)) %}
{% endmacro %}

{% macro it_compat_schema(schema=none, compat_schema=none) %}
  {% do _it_result(icecore._icecore_compat_schema(schema, compat_schema)) %}
{% endmacro %}

{% macro it_create_compat_views(schema=none, compat='elementary', compat_schema=none) %}
  {% do _it_result(icecore.create_compat_views(schema, compat, compat_schema)) %}
{% endmacro %}

{#- Send items to the obs schema with emit_run, as end_run would (seeds records for the views). -#}
{% macro it_compat_emit_items(items) %}
  {% do _it_result(icecore.emit_run(items)) %}
{% endmacro %}
