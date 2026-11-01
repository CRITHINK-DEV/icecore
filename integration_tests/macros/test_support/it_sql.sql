{#- Test wrappers for icecore._sql and every renderer that carries a value into SQL (tests/test_sql_safety.py). -#}

{#- dbt reads --args as YAML, which cannot carry every payload, so the values come in the environment variable IT_PAYLOADS (JSON). -#}
{% macro it_sql(context) %}
  {% set values = fromjson(env_var('IT_PAYLOADS')) %}
  {% set out = [] %}
  {% for v in values %}
    {% do out.append({'sql': icecore._sql(v, context) if icecore._sql_error(v, context) is none or context == 'string' else none,
                      'error': icecore._sql_error(v, context) if context != 'string' else none}) %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}

{#- Every renderer that takes a string value, with each payload as that value; a body wrapper puts the result where a
    task or alert body would hold it. Returns {renderer: [sql per payload]}. Nothing runs. -#}
{% macro it_string_renderers() %}
  {% set payloads = fromjson(env_var('IT_PAYLOADS')) %}
  {% set out = {} %}
  {% set rel = api.Relation.create(database='D', schema='S', identifier='T') %}
  {% for name in ['literal', 'guarded_call', 'guarded_ddl', 'try_show', 'try_scalar', 'columns_block', 'timestamp',
                  'changes_source', 'body'] %}
    {% do out.update({name: []}) %}
  {% endfor %}
  {% for p in payloads %}
    {% do out['literal'].append('select ' ~ icecore._icecore_sql_literal(p) ~ ', ' ~ icecore._icecore_schema_literal(p)
        ~ ', ' ~ icecore._icecore_block_literal(p)) %}
    {% do out['guarded_call'].append(icecore._icecore_guarded_call_sql(icecore._icecore_procedure('ICECORE_EMIT'),
        [{'name': p, 'attrs': {'a': p, p: p}}])) %}
    {% do out['guarded_ddl'].append('execute immediate ' ~ icecore._sql(icecore._icecore_guarded_ddl_block('select ' ~ icecore._sql(p, 'string')), 'string')) %}
    {% do out['try_show'].append('execute immediate ' ~ icecore._sql(icecore._icecore_try_show_block('show classes like ' ~ icecore._sql(p, 'string')), 'string')) %}
    {% do out['try_scalar'].append('execute immediate ' ~ icecore._sql(icecore._icecore_try_scalar_block('select ' ~ icecore._sql(p, 'string')), 'string')) %}
    {% do out['columns_block'].append('execute immediate ' ~ icecore._sql(icecore._icecore_columns_signature_block(icecore._sql(['D', 'S', 'T'], 'ident')), 'string')) %}
    {% do out['timestamp'].append('select ' ~ icecore._sql_timestamp(p)) %}
    {% do out['changes_source'].append('select count(*) from ' ~ icecore._icecore_changes_source(rel, p)) %}
    {% do out['body'].append('CREATE TASK t AS EXECUTE IMMEDIATE $$ BEGIN SELECT ' ~ icecore._sql(p, 'string') ~ '; END; $$') %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}
