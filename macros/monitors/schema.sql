{#- Zero-scan schema signatures and diffs (UC2).
    They return values only; emitting the schema-change LOG records is the monitors' job.
    SHOW COLUMNS is a metadata operation: no warehouse scan. The read runs inside a Snowflake Scripting
    block that catches errors, so a missing relation or privilege returns none and logs a warning. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro get_columns_signature(relation) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set name = icecore._icecore_schema_quoted_name(relation) -%}
  {%- set block = icecore._icecore_columns_signature_block(name) -%}
  {%- set out = fromjson(run_query('execute immediate ' ~ icecore._sql(block, 'string')).rows[0][0]) -%}
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

{#- Columns -> lists of columns whose JSON, as an attribute string (quotes escaped, as the payload cap measures it),
    stays within the payload cap minus `overhead` (the measured size of the record's other attributes), so a record never reaches the cap and no column is ever cut. A table that fits
    gives one chunk. No columns gives one empty chunk (the snapshot of an empty table is still a record). -#}
{% macro _icecore_signature_chunks(columns, cap_bytes=none, overhead=0) %}
  {%- set cap = cap_bytes if cap_bytes is not none else icecore._icecore_payload_cap_bytes() -%}
  {%- set budget = [cap - overhead, 256] | max -%}
  {%- set chunks = [] -%}
  {%- set ns = namespace(current=[], size=4) -%}
  {%- for c in columns -%}
    {%- set size = (tojson(tojson(c)) | length) + 1 -%}
    {%- if ns.current and ns.size + size > budget -%}
      {%- do chunks.append(ns.current) -%}
      {%- set ns.current = [] -%}
      {%- set ns.size = 4 -%}
    {%- endif -%}
    {%- do ns.current.append(c) -%}
    {%- set ns.size = ns.size + size -%}
  {%- endfor -%}
  {%- if ns.current or not chunks -%}{%- do chunks.append(ns.current) -%}{%- endif -%}
  {{ return(chunks) }}
{% endmacro %}


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
    {%- do quoted.append(icecore._sql(part, 'name')) -%}
  {%- endfor -%}
  {{ return(quoted | join('.')) }}
{% endmacro %}


{#- Wrapper kept for one release: icecore._sql(value, 'string'). -#}
{% macro _icecore_schema_literal(value) %}
  {{ return(icecore._sql(value, 'string')) }}
{% endmacro %}


{#- The Scripting block of get_columns_signature: constant text around the one quoted name (icecore._sql). -#}
{% macro _icecore_columns_signature_block(name) %}
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
  {{ return(block) }}
{% endmacro %}
