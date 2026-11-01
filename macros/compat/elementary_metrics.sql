{#- UC8 compat views `data_monitoring_metrics` and `schema_columns_snapshot`, with the columns, order and types of
    Elementary's tables of those names (Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION, macros
    empty_data_monitoring_metrics and empty_schema_columns_snapshot). They select from the core views METRICS and
    SCHEMA_CHANGES of the obs schema and nothing else (no base_events).

    data_monitoring_metrics, one row per METRICS row (row counts match):
      id                     md5 of relation, metric, column, dimension and observed_at
      full_table_name        the relation as Elementary writes it: DB.SCHEMA.NAME, upper case, no quotes
      column_name            "COLUMN";  metric_name and metric_type: metric (Elementary's metric_type is the metric's
                             own name for table and column metrics)
      metric_value           value as FLOAT
      bucket_start, bucket_end, updated_at, created_at   observed_at (a point metric has no bucket of its own)
      dimension, dimension_value   icecore's `name=value` dimension split at the first `=` (NULL without one)
      Typed NULLs: source_value, bucket_duration_hours, metric_properties.
    schema_columns_snapshot, one row per SCHEMA_CHANGES row that leaves the column in the table (added and
    type_changed; a `removed` change leaves no column state, so it has no row).
    IT IS A CHANGE LOG, NOT A SNAPSHOT: Elementary appends one row per current column of a table on every run, so
    "the latest snapshot per table" (the rows of its newest detected_at) lists every column. Here a column has a row
    only when it was added or its type changed, so that query returns only the columns changed in the latest change.
    A column that never changed has the row of its own first appearance, or none if it predates icecore's monitoring.
    A full per-run column-state emission would be needed to match Elementary (not built; follow-up):
      column_state_id        md5 of full_table_name, column_name and data_type
      full_column_name       upper(full_table_name.column_name);  full_table_name as above
      column_name, data_type to_type
      is_new                 change_kind = 'added'
      detected_at, created_at   detected_at -#}


{#- The group of views appended to the compat list: [{name, sql}]. -#}
{% macro _icecore_elementary_metric_views(obs) %}
  {{ return([
    {'name': 'DATA_MONITORING_METRICS', 'sql': icecore.elementary_data_monitoring_metrics_view_sql(obs)},
    {'name': 'SCHEMA_COLUMNS_SNAPSHOT', 'sql': icecore.elementary_schema_columns_snapshot_view_sql(obs)},
  ]) }}
{% endmacro %}


{% macro elementary_data_monitoring_metrics_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_elementary_metrics_columns(), obs ~ '."METRICS"')) }}
{% endmacro %}


{% macro elementary_schema_columns_snapshot_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_elementary_schema_snapshot_columns(), obs ~ '."SCHEMA_CHANGES"',
      where="t.change_kind in ('added', 'type_changed')")) }}
{% endmacro %}


{#- DB.SCHEMA.NAME as Elementary writes a relation: icecore's quoted "DB"."SCHEMA"."NAME", unquoted and upper case. -#}
{% macro _icecore_elementary_full_table_name(relation_sql) %}
  {{ return("upper(replace(" ~ relation_sql ~ ", '\"', ''))") }}
{% endmacro %}


{% macro _icecore_elementary_metrics_columns() %}
  {%- set table = icecore._icecore_elementary_full_table_name('t.relation') -%}
  {{ return([
    {'name': 'id', 'type': 'string', 'expr': "md5(concat_ws('|', t.relation, t.metric, coalesce(t.\"COLUMN\", ''), coalesce(t.dimension, ''), to_varchar(t.observed_at)))"},
    {'name': 'full_table_name', 'type': 'string', 'expr': table},
    {'name': 'column_name', 'type': 'string', 'expr': 't."COLUMN"'},
    {'name': 'metric_name', 'type': 'string', 'expr': 't.metric'},
    {'name': 'metric_type', 'type': 'string', 'expr': 't.metric'},
    {'name': 'metric_value', 'type': 'float', 'expr': 't.value'},
    {'name': 'source_value', 'type': 'string', 'expr': none},
    {'name': 'bucket_start', 'type': 'timestamp', 'expr': 't.observed_at'},
    {'name': 'bucket_end', 'type': 'timestamp', 'expr': 't.observed_at'},
    {'name': 'bucket_duration_hours', 'type': 'int', 'expr': none},
    {'name': 'updated_at', 'type': 'timestamp', 'expr': 't.observed_at'},
    {'name': 'dimension', 'type': 'string', 'expr': "iff(t.dimension is null, null, split_part(t.dimension, '=', 1))"},
    {'name': 'dimension_value', 'type': 'string', 'expr': "iff(contains(t.dimension, '='), substr(t.dimension, charindex('=', t.dimension) + 1), null)"},
    {'name': 'metric_properties', 'type': 'string', 'expr': none},
    {'name': 'created_at', 'type': 'timestamp', 'expr': 't.observed_at'},
  ]) }}
{% endmacro %}


{% macro _icecore_elementary_schema_snapshot_columns() %}
  {%- set table = icecore._icecore_elementary_full_table_name('t.relation') -%}
  {{ return([
    {'name': 'column_state_id', 'type': 'string', 'expr': "md5(concat_ws('|', " ~ table ~ ", t.\"COLUMN\", coalesce(t.to_type, '')))"},
    {'name': 'full_column_name', 'type': 'string', 'expr': "upper(" ~ table ~ " || '.' || t.\"COLUMN\")"},
    {'name': 'full_table_name', 'type': 'string', 'expr': table},
    {'name': 'column_name', 'type': 'string', 'expr': 't."COLUMN"'},
    {'name': 'data_type', 'type': 'string', 'expr': 't.to_type'},
    {'name': 'is_new', 'type': 'boolean', 'expr': "t.change_kind = 'added'"},
    {'name': 'detected_at', 'type': 'timestamp', 'expr': 't.detected_at'},
    {'name': 'created_at', 'type': 'timestamp', 'expr': 't.detected_at'},
  ]) }}
{% endmacro %}
