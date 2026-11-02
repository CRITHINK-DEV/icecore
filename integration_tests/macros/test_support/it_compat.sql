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

{#- The SELECTs and columns of data_monitoring_metrics and schema_columns_snapshot. -#}
{% macro it_compat_metrics() %}
  {% do _it_result({
      'metrics': {'sql': icecore.elementary_data_monitoring_metrics_view_sql('"DB"."OBS"'),
                  'columns': icecore._icecore_elementary_metrics_columns()},
      'snapshot': {'sql': icecore.elementary_schema_columns_snapshot_view_sql('"DB"."OBS"'),
                   'columns': icecore._icecore_elementary_schema_snapshot_columns()}}) %}
{% endmacro %}

{#- Write span events to the obs schema with emit_events (seeds METRICS and SCHEMA_CHANGES rows). -#}
{% macro it_compat_emit_events(events) %}
  {% do _it_result(icecore.emit_events(events)) %}
{% endmacro %}

{#- The SELECT of the core schema_columns view (obs schema qualifiers are placeholders). -#}
{% macro it_schema_columns_view() %}
  {% do _it_result(icecore.schema_columns_view_sql('"DB"."TEL"."EVENTS"', 'DB', 'OBS')) %}
{% endmacro %}

{#- schema_changes' split of a column list under the payload cap. -#}
{% macro it_signature_chunks(columns, cap_bytes) %}
  {% do _it_result(icecore._icecore_signature_chunks(columns, cap_bytes)) %}
{% endmacro %}

{#- The SELECT of the core schema_columns view against the real event table and the obs schema. -#}
{% macro it_schema_columns_view_real(schema) %}
  {% do _it_result(icecore.schema_columns_view_sql(obs_schema=schema)) %}
{% endmacro %}

{#- The compat schema_columns_snapshot SELECT over the SCHEMA_COLUMNS view of the (qualified) obs schema. -#}
{% macro it_compat_snapshot_sql(obs) %}
  {% do _it_result(icecore.elementary_schema_columns_snapshot_view_sql(obs)) %}
{% endmacro %}
