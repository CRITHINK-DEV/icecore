{#- Spike S5 probes: zero-scan metadata per table kind. Spike-only: s5_write inserts into SPIKES tables.
    `relation` is always a fully quoted three-part name ("DB"."SCHEMA"."Name"), embedded quotes doubled.
    Results are returned and also logged as `icecore_it_result:<json>` so pytest can read them from run-operation. -#}

{% macro s5_log_result(value) %}
  {% do log('icecore_it_result:' ~ tojson(value), info=True) %}
{% endmacro %}

{% macro s5_literal(value) %}
  {{ return("'" ~ (value | string | replace('\\', '\\\\') | replace("'", "\\'")) ~ "'") }}
{% endmacro %}

{#- Split a three-part name into unquoted parts: quoted parts keep their case and undouble "",
    unquoted parts resolve to upper case, as Snowflake does. -#}
{% macro s5_split_relation(relation) %}
  {%- set ns = namespace(parts=[], cur='', inq=false, skip=false) -%}
  {%- for i in range(relation | length) -%}
    {%- set ch = relation[i] -%}
    {%- if ns.skip -%}
      {%- set ns.skip = false -%}
    {%- elif ns.inq and ch == '"' and relation[i + 1:i + 2] == '"' -%}
      {%- set ns.cur = ns.cur ~ '"' -%}
      {%- set ns.skip = true -%}
    {%- elif ns.inq and ch == '"' -%}
      {%- set ns.inq = false -%}
    {%- elif ns.inq -%}
      {%- set ns.cur = ns.cur ~ ch -%}
    {%- elif ch == '"' -%}
      {%- set ns.inq = true -%}
    {%- elif ch == '.' -%}
      {%- set ns.parts = ns.parts + [ns.cur] -%}
      {%- set ns.cur = '' -%}
    {%- else -%}
      {%- set ns.cur = ns.cur ~ (ch | upper) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set parts = ns.parts + [ns.cur] -%}
  {%- if parts | length != 3 -%}
    {{ exceptions.raise_compiler_error('s5: expected a three-part name, got ' ~ relation) }}
  {%- endif -%}
  {{ return(parts) }}
{% endmacro %}

{% macro s5_metadata_sql(relation) %}
  {%- set parts = s5_split_relation(relation) -%}
  {%- set ts_fmt = "'YYYY-MM-DD\"T\"HH24:MI:SS.FF6\"Z\"'" -%}
  {%- set sql -%}
select
    i.row_count,
    case when c.token > 0 then to_varchar(to_timestamp_ntz(c.token, 9), {{ ts_fmt }}) end as last_change,
    c.token::varchar as last_change_token,
    to_varchar(convert_timezone('UTC', i.last_altered), {{ ts_fmt }}) as last_altered,
    i.table_type,
    i.is_transient,
    i.is_dynamic,
    i.is_iceberg
from (select system$last_change_commit_time({{ s5_literal(relation) }})::number(38, 0) as token) c
left join {{ adapter.quote(parts[0] | replace('"', '""')) }}.information_schema.tables i
    on i.table_schema = {{ s5_literal(parts[1]) }}
   and i.table_name = {{ s5_literal(parts[2]) }}
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}

{% macro s5_metadata(relation) %}
  {%- if not execute -%}{{ return({}) }}{%- endif -%}
  {%- set row = run_query(s5_metadata_sql(relation)).rows[0] -%}
  {%- set result = {
      'row_count': none if row[0] is none else row[0] | int,
      'last_change': row[1],
      'last_change_token': row[2],
      'last_altered': row[3],
      'table_type': row[4],
      'is_transient': row[5],
      'is_dynamic': row[6],
      'is_iceberg': row[7],
  } -%}
  {% do s5_log_result(result) %}
  {{ return(result) }}
{% endmacro %}

{% macro s5_write(relation, n) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {% do run_query(
      'insert into ' ~ relation ~ ' (id, note) select seq4(), ' ~ s5_literal('s5')
      ~ ' from table(generator(rowcount => ' ~ (n | int) ~ '))'
  ) %}
  {% do s5_log_result({'written': n | int}) %}
{% endmacro %}

{#- What adapter.quote makes of an identifier that contains double quotes. -#}
{% macro s5_adapter_quote(identifier) %}
  {% do s5_log_result(adapter.quote(identifier)) %}
{% endmacro %}
