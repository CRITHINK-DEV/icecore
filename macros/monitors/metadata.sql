{#- Zero-scan freshness and row-count readers (UC2).
    They return values only; emitting METRIC / LOG records is the monitors' job.
    Every read runs inside a Snowflake Scripting block that catches errors, so a missing table or a
    missing privilege returns none and logs a warning instead of failing the user's run.
    Spike S5 (spikes/S5.md) found ROW_COUNT and SYSTEM$LAST_CHANGE_COMMIT_TIME correct within seconds for
    standard, transient and dynamic tables. Iceberg is unmeasured, so its metadata total is not trusted by default.
    get_row_count_changes is not zero-scan: cheap for insert-only windows, but after DELETE / UPDATE / MERGE it
    can read more than a full scan (S5). The total (get_row_count_metadata) and the delta over CHANGES (get_row_count_changes) are distinct values:
    the volume test emits them as distinct metrics, row_count and rows_added, never mixed in one history. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro get_last_change_time(relation) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set sql -%}
    select case when t.token > 0
                then to_varchar(to_timestamp_ntz(t.token, 9), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"') end
    from (select system$last_change_commit_time({{ icecore._icecore_sql_literal(icecore._icecore_quoted_name(relation)) }})::number(38, 0) as token) t
  {%- endset -%}
  {{ return(icecore._icecore_safe_scalar(sql, 'get_last_change_time', relation)) }}
{% endmacro %}


{% macro get_row_count_metadata(relation) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set info = icecore._icecore_table_info(relation) -%}
  {%- if info is none -%}{{ return(none) }}{%- endif -%}
  {%- set name = icecore._icecore_quoted_name(relation) -%}
  {%- if info.get('row_count') is none -%}
    {% do log('icecore: warning: ' ~ name ~ ' is a ' ~ info.get('kind') ~ ': zero-scan volume is not supported for views and external tables (no ROW_COUNT in metadata)', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- if info.get('kind') in icecore._icecore_metadata_fallback_kinds() -%}
    {% do log('icecore: ' ~ name ~ ' is a ' ~ info.get('kind') ~ ' table: its metadata row count is not trusted (spike S5); use get_row_count_changes for rows added', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(info['row_count'] | int) }}
{% endmacro %}


{% macro get_row_count_changes(relation, since) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- if since is none -%}
    {% do log('icecore: warning: get_row_count_changes needs since (the previous observation time)', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(icecore._icecore_row_delta_from_changes(relation, since)) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- Kinds whose metadata row count is not trusted (S5). The default lives in dbt_project.yml
    (vars: icecore: metadata_fallback_kinds). A project that overrides the icecore var replaces the whole dict,
    so the same default is applied when the key is absent, until a shared config accessor merges defaults. -#}
{% macro _icecore_metadata_fallback_kinds() %}
  {{ return(var('icecore', {}).get('metadata_fallback_kinds', ['iceberg'])) }}
{% endmacro %}


{#- Name parts as Snowflake resolves them: quoted parts keep their case, unquoted ones are upper-cased. -#}
{% macro _icecore_name_parts(relation) %}
  {%- set parts = [] -%}
  {%- for key, value in [('database', relation.database), ('schema', relation.schema), ('identifier', relation.identifier)] -%}
    {%- do parts.append(value if relation.quote_policy.get_part(key) else value | upper) -%}
  {%- endfor -%}
  {{ return(parts) }}
{% endmacro %}


{#- Fully quoted three-part name with embedded double quotes doubled (adapter.quote does not double them, S5 finding 4). -#}
{% macro _icecore_quoted_name(relation) %}
  {%- set quoted = [] -%}
  {%- for part in icecore._icecore_name_parts(relation) -%}
    {%- do quoted.append(icecore._sql(part, 'name')) -%}
  {%- endfor -%}
  {{ return(quoted | join('.')) }}
{% endmacro %}


{#- Wrapper kept for one release: icecore._sql(value, 'string'). -#}
{% macro _icecore_sql_literal(value) %}
  {{ return(icecore._sql(value, 'string')) }}
{% endmacro %}


{#- Run a one-value SELECT inside a Snowflake Scripting block that catches every error:
    returns {'value': <value>} or {'error': <Snowflake message>, 'code': <SQLCODE>}, never raises. -#}
{% macro _icecore_try_scalar(select_sql) %}
  {{ return(fromjson(run_query('execute immediate ' ~ icecore._sql(icecore._icecore_try_scalar_block(select_sql), 'string')).rows[0][0])) }}
{% endmacro %}


{#- The block of _icecore_try_scalar: constant text around the one SELECT it is given. -#}
{% macro _icecore_try_scalar_block(select_sql) %}
  {%- set block -%}
declare
  v variant default null;
begin
  select x into :v from (select ({{ select_sql }})::variant as x);
  return object_construct('value', v);
exception
  when other then return object_construct('error', sqlerrm, 'code', sqlcode);
end;
  {%- endset -%}
  {{ return(block) }}
{% endmacro %}


{#- Run a one-value SELECT so that no error reaches the caller: returns the value, or none (and logs a warning). -#}
{% macro _icecore_safe_scalar(select_sql, what, relation) %}
  {%- set out = icecore._icecore_try_scalar(select_sql) -%}
  {%- if 'error' in out -%}
    {% do log('icecore: warning: ' ~ what ~ ' could not read ' ~ icecore._icecore_quoted_name(relation) ~ ': ' ~ out['error'], info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(out.get('value')) }}
{% endmacro %}


{#- INFORMATION_SCHEMA.TABLES row for the relation as {row_count, kind}, or none when it is not found. -#}
{% macro _icecore_table_info(relation) %}
  {%- set parts = icecore._icecore_name_parts(relation) -%}
  {%- set sql -%}
    select object_construct(
        'row_count', row_count,
        'kind', case
            when is_iceberg = 'YES' then 'iceberg'
            when is_dynamic = 'YES' then 'dynamic'
            when table_type = 'VIEW' then 'view'
            when table_type = 'MATERIALIZED VIEW' then 'materialized_view'
            when table_type = 'EXTERNAL TABLE' then 'external'
            when table_type in ('TEMPORARY TABLE', 'LOCAL TEMPORARY') then 'temporary'
            when is_transient = 'YES' then 'transient'
            else 'standard' end)
    from {{ icecore._sql(parts[0], 'name') }}.information_schema.tables
    where table_schema = {{ icecore._icecore_sql_literal(parts[1]) }}
      and table_name = {{ icecore._icecore_sql_literal(parts[2]) }}
  {%- endset -%}
  {%- set info = icecore._icecore_safe_scalar(sql, 'get_row_count_metadata', relation) -%}
  {%- if info is none -%}
    {% do log('icecore: warning: ' ~ icecore._icecore_quoted_name(relation) ~ ' is not in INFORMATION_SCHEMA.TABLES (missing or not visible to this role)', info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- set parsed = fromjson(info) if info is string else info -%}
  {{ return(parsed if parsed is mapping else none) }}
{% endmacro %}


{#- The CHANGES source for everything icecore reads since `since`: the rows-added count below and the column
    monitors (columns.sql). information => default: an UPDATE shows as a DELETE of the old row version and an INSERT
    of the new one; rows inserted and deleted inside the window cancel out. Needs change tracking on the table, or
    on the view and its base tables, covering the whole window.
    Cost (S5): only the new micro-partitions for insert-only windows; old and new versions of every rewritten
    micro-partition after DELETE / UPDATE / MERGE, about 1.9 times a full scan in S5. -#}
{% macro _icecore_changes_source(relation, since) %}
  {{ return(icecore._icecore_quoted_name(relation)
      ~ ' changes (information => default) at (timestamp => to_timestamp_tz(' ~ icecore._icecore_sql_literal(since) ~ '))') }}
{% endmacro %}


{#- Net rows added since `since` (inserts minus deletes) from CHANGES. -#}
{% macro _icecore_row_delta_sql(relation, since) %}
  {{ return("select count_if(metadata$action = 'INSERT') - count_if(metadata$action = 'DELETE') from "
      ~ icecore._icecore_changes_source(relation, since)) }}
{% endmacro %}


{% macro _icecore_row_delta_from_changes(relation, since) %}
  {%- set value = icecore._icecore_safe_scalar(icecore._icecore_row_delta_sql(relation, since), 'get_row_count_changes', relation) -%}
  {{ return(none if value is none else value | int) }}
{% endmacro %}
