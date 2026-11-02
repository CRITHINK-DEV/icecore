{#- UC5 create_views: creates the icecore views in the obs schema (spec section 7; setup step `create_views`).

    One list, _icecore_views(), holds every view: [{name, sql}]. A later story appends its view there and
    nothing else changes. Each view is CREATE OR REPLACE ... COPY GRANTS, so a second run leaves the grants
    an admin made on them. The event table is resolved once and shared by all views. No tables are created. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Creates every view in <target db>.<schema>; returns their qualified names (empty at parse time). -#}
{% macro create_views(schema=none) %}
  {%- set created = [] -%}
  {%- if execute -%}
    {%- for view in icecore._icecore_views(schema) -%}
      {%- do run_query(icecore._icecore_view_ddl(view['name'], view['sql'], schema)) -%}
      {%- do created.append(icecore._icecore_procedure(view['name'], schema)) -%}
    {%- endfor -%}
    {%- do log('icecore: created ' ~ (created | join(', ')), info=True) -%}
  {%- endif -%}
  {{ return(created) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The one list of views: [{name, sql}], names upper case. Append a view here. -#}
{% macro _icecore_views(schema=none, event_table=none) %}
  {%- set event_table = event_table or icecore.resolve_event_table(target.database) -%}
  {%- set obs = icecore.obs_schema_name(schema) -%}
  {{ return([
    {'name': 'METRICS', 'sql': icecore.metrics_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'SCHEMA_CHANGES', 'sql': icecore.schema_changes_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'TEST_RESULTS', 'sql': icecore.test_results_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'LINEAGE', 'sql': icecore.lineage_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'LINEAGE_LATEST', 'sql': icecore.lineage_latest_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'CAPABILITIES', 'sql': icecore.capabilities_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'INVOCATIONS', 'sql': icecore.invocations_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'NODE_EXECUTIONS', 'sql': icecore.node_executions_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'SILENCE_EVENTS', 'sql': icecore.silence_events_view_sql(event_table=event_table, obs_schema=obs)},
    {'name': 'ALERTS_FEED', 'sql': icecore.alerts_feed_view_sql(event_table=event_table, obs_schema=obs)},
  ]) }}
{% endmacro %}


{% macro _icecore_view_ddl(name, sql, schema=none) %}
  {{ return('CREATE OR REPLACE VIEW ' ~ icecore._icecore_procedure(name, schema) ~ ' COPY GRANTS AS\n' ~ sql) }}
{% endmacro %}
