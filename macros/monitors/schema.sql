{#- Zero-scan schema signatures and diffs (UC2).
    They return values only; emitting the schema-change LOG records is the monitors' job.
    SHOW COLUMNS is a metadata operation: no warehouse scan. The read runs inside a Snowflake Scripting
    block that catches errors, so a missing relation or privilege returns none and logs a warning. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro get_columns_signature(relation) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set name = icecore._icecore_schema_quoted_name(relation) -%}
  {%- set block -%}
declare
  v variant default null;
  qid varchar;
begin
  show columns in table {{ name }};
  qid := sqlid;
  select array_agg(object_construct('name', "column_name", 'data_type', {{ icecore._icecore_schema_type_sql('parse_json("data_type")') }}))
    into :v
    from table(result_scan(:qid));
  return object_construct('value', v);
exception
  when other then return object_construct('error', sqlerrm);
end;
  {%- endset -%}
  {%- set out = fromjson(run_query('execute immediate ' ~ icecore._icecore_schema_literal(block)).rows[0][0]) -%}
  {%- if 'error' in out -%}
    {% do log('icecore: warning: get_columns_signature could not read ' ~ name ~ ': ' ~ out['error'], info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {{ return(out.get('value')) }}
{% endmacro %}


{% macro diff_schema(previous, current) %}
  {%- set result = {'added': [], 'removed': [], 'type_changed': []} -%}
  {%- if previous is none or current is none -%}{{ return(result) }}{%- endif -%}
  {%- set before = {} -%}
  {%- for c in previous -%}{%- do before.update({c['name']: c['data_type']}) -%}{%- endfor -%}
  {%- set after = {} -%}
  {%- for c in current -%}{%- do after.update({c['name']: c['data_type']}) -%}{%- endfor -%}
  {%- for c in current -%}
    {%- if c['name'] not in before -%}
      {%- do result['added'].append({'name': c['name'], 'data_type': c['data_type']}) -%}
    {%- elif before[c['name']] != c['data_type'] -%}
      {%- do result['type_changed'].append({'name': c['name'], 'from_type': before[c['name']], 'to_type': c['data_type']}) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- for c in previous -%}
    {%- if c['name'] not in after -%}
      {%- do result['removed'].append({'name': c['name'], 'data_type': c['data_type']}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(result) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- SQL expression turning SHOW COLUMNS' data_type JSON into a type string, e.g. NUMBER(38,0), VARCHAR(16777216). -#}
{% macro _icecore_schema_type_sql(t) %}
  {%- set sql -%}
case {{ t }}:type::string
  when 'FIXED' then 'NUMBER(' || {{ t }}:precision::string || ',' || {{ t }}:scale::string || ')'
  when 'TEXT' then 'VARCHAR(' || {{ t }}:length::string || ')'
  when 'BINARY' then 'BINARY(' || {{ t }}:length::string || ')'
  when 'REAL' then 'FLOAT'
  when 'TIMESTAMP_NTZ' then 'TIMESTAMP_NTZ(' || {{ t }}:scale::string || ')'
  when 'TIMESTAMP_LTZ' then 'TIMESTAMP_LTZ(' || {{ t }}:scale::string || ')'
  when 'TIMESTAMP_TZ' then 'TIMESTAMP_TZ(' || {{ t }}:scale::string || ')'
  when 'TIME' then 'TIME(' || {{ t }}:scale::string || ')'
  else {{ t }}:type::string
end
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Fully quoted three-part name: quoted parts keep their case, unquoted ones are upper-cased, embedded
    double quotes are doubled (adapter.quote does not double them, spike S5). -#}
{% macro _icecore_schema_quoted_name(relation) %}
  {%- set quoted = [] -%}
  {%- for key, value in [('database', relation.database), ('schema', relation.schema), ('identifier', relation.identifier)] -%}
    {%- set part = value if relation.quote_policy.get_part(key) else value | upper -%}
    {%- do quoted.append('"' ~ (part | replace('"', '""')) ~ '"') -%}
  {%- endfor -%}
  {{ return(quoted | join('.')) }}
{% endmacro %}


{#- Single-quoted Snowflake string literal: backslash first, then single quote. -#}
{% macro _icecore_schema_literal(value) %}
  {{ return("'" ~ (value | string | replace('\\', '\\\\') | replace("'", "\\'")) ~ "'") }}
{% endmacro %}
