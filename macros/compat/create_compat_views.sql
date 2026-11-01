{#- UC8 create_compat_views: creates the Elementary-compatible views in the compat schema (spec section 7;
    setup step `create_compat_views`, run when setup gets `compat: elementary`).

    One list, _icecore_compat_views(), holds every compat view: [{name, sql}]. A later UC8 story appends its views
    there (one `{% do views.extend(icecore._icecore_elementary_<group>_views(obs)) %}` line) and nothing else
    changes. Each view is CREATE OR REPLACE ... COPY GRANTS, so a second run leaves the grants an admin made on
    them. The views select from icecore's core views in the obs schema (TEST_RESULTS, METRICS, SCHEMA_CHANGES,
    LINEAGE_LATEST), never from base_events, so the core views must exist: setup creates them first. They match
    the relation names, column names, column order and types of the Elementary dbt package pinned in
    macros/compat/ELEMENTARY_VERSION; a column icecore cannot fill is a typed NULL. No tables are created.

    The compat schema is var icecore.compat_schema, default <obs schema>_ELEMENTARY (the name Elementary's own
    schema has next to a project schema). It is separate from the obs schema, so a dashboard or `edr` profile
    that points at it sees Elementary's relations and nothing else. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Creates the compat schema (CREATE SCHEMA IF NOT EXISTS) and every compat view in it; returns the qualified
    names of the views (empty at parse time). `compat` names the product the views imitate: only `elementary`. -#}
{% macro create_compat_views(schema=none, compat='elementary', compat_schema=none) %}
  {%- set created = [] -%}
  {%- if (compat or 'elementary') | string | lower != 'elementary' -%}
    {{ exceptions.raise_compiler_error("icecore: compat must be 'elementary' (or 'none' for no compat views), got " ~ compat) }}
  {%- endif -%}
  {%- if execute -%}
    {%- set target_schema = icecore._icecore_compat_schema(schema, compat_schema) -%}
    {%- set statement = 'CREATE SCHEMA IF NOT EXISTS ' ~ target_schema -%}
    {%- set status = icecore._icecore_guarded_ddl(statement) -%}
    {%- if status != 'ok' -%}
      {% do log('icecore: warning: ' ~ statement ~ ' failed (' ~ status ~ '); the role needs CREATE SCHEMA on ' ~ target.database ~ ', or an admin must create the compat schema', info=True) %}
    {%- endif -%}
    {%- for view in icecore._icecore_compat_views(schema) -%}
      {%- do run_query(icecore._icecore_compat_view_ddl(view['name'], view['sql'], schema, compat_schema)) -%}
      {%- do created.append(target_schema ~ '."' ~ view['name'] ~ '"') -%}
    {%- endfor -%}
    {%- do log('icecore: created ' ~ (created | join(', ')), info=True) -%}
  {%- endif -%}
  {{ return(created) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The one list of compat views: [{name, sql}], names upper case. Append a group of views here. -#}
{% macro _icecore_compat_views(schema=none) %}
  {%- set obs = icecore.obs_schema(schema) -%}
  {%- set views = [] -%}
  {%- do views.extend(icecore._icecore_elementary_test_views(obs)) -%}
  {%- do views.extend(icecore._icecore_elementary_run_views(obs)) -%}
  {{ return(views) }}
{% endmacro %}


{#- The compat schema, fully qualified and quoted: "<TARGET_DB>"."<COMPAT_SCHEMA>". An explicit name or the var
    icecore.compat_schema wins; the default is <obs schema name>_ELEMENTARY. -#}
{% macro _icecore_compat_schema(schema=none, compat_schema=none) %}
  {%- set name = compat_schema or var('icecore', {}).get('compat_schema') or (icecore.obs_schema_name(schema) ~ '_ELEMENTARY') -%}
  {{ return('"' ~ (target.database | string | upper | replace('"', '""')) ~ '"."' ~ (name | string | upper | replace('"', '""')) ~ '"') }}
{% endmacro %}


{% macro _icecore_compat_view_ddl(name, sql, schema=none, compat_schema=none) %}
  {{ return('CREATE OR REPLACE VIEW ' ~ icecore._icecore_compat_schema(schema, compat_schema) ~ '."' ~ name ~ '" COPY GRANTS AS\n' ~ sql) }}
{% endmacro %}


{#- The Snowflake type Elementary's own tables get for its logical column types (empty_table in the Elementary
    package: string and long_string are varchar, bigint and int NUMBER(38,0), float FLOAT, timestamp TIMESTAMP_NTZ). -#}
{% macro _icecore_elementary_sql_type(logical) %}
  {%- set types = {'string': 'varchar', 'long_string': 'varchar', 'bigint': 'bigint', 'int': 'int', 'float': 'float',
                   'timestamp': 'timestamp_ntz', 'boolean': 'boolean'} -%}
  {%- if logical not in types -%}
    {{ exceptions.raise_compiler_error('icecore: no Snowflake type for the Elementary column type ' ~ logical) }}
  {%- endif -%}
  {{ return(types[logical]) }}
{% endmacro %}


{#- SELECT of a compat view: columns is [{name, type, expr}] in Elementary's order, expr a SQL expression over
    `source` (an alias of the core view) or none for a column icecore cannot fill (a typed NULL). Every column is
    cast to its Elementary type, so DESCRIBE shows Elementary's types whatever the core view returns. -#}
{% macro _icecore_compat_select(columns, source_sql, alias='t', where=none) %}
  {%- set parts = [] -%}
  {%- for column in columns -%}
    {%- do parts.append('    cast(' ~ (column['expr'] if column['expr'] is not none else 'null') ~ ' as '
        ~ icecore._icecore_elementary_sql_type(column['type']) ~ ') as ' ~ column['name']) -%}
  {%- endfor -%}
  {{ return('select\n' ~ (parts | join(',\n')) ~ '\nfrom ' ~ source_sql ~ ' as ' ~ alias ~ (('\nwhere ' ~ where) if where else '')) }}
{% endmacro %}
