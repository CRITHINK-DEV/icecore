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

{#- Every renderer that carries an object NAME (the 'name' context: a database, schema or table of the user's project),
    with each payload as that name. A payload `_sql` refuses (a `$` or a control character) is reported as refused and
    never rendered. Returns {renderer: [sql or none per payload], 'refused': [error or none per payload]}. -#}
{% macro it_name_renderers() %}
  {% set payloads = fromjson(env_var('IT_PAYLOADS')) %}
  {% set out = {'refused': []} %}
  {% set names = ['procedure_guarded_call', 'columns_block', 'try_show', 'try_scalar', 'guarded_ddl', 'relation_quoted_name',
                  'schema_quoted_name', 'changes_source', 'body'] %}
  {% for name in names %}
    {% do out.update({name: []}) %}
  {% endfor %}
  {% for p in payloads %}
    {% set error = icecore._sql_error(p, 'name') %}
    {% do out['refused'].append(error) %}
    {% if error is none %}
      {% set rel = api.Relation.create(database=p, schema=p, identifier=p, quote_policy={'database': true, 'schema': true, 'identifier': true}) %}
      {% set quoted = icecore._sql([p, 'S', p], 'name') %}
      {% do out['procedure_guarded_call'].append(icecore._icecore_guarded_call_sql(icecore._sql([p, 'ICECORE_OBS', 'ICECORE_EMIT'], 'name'), [{'name': 'x', 'attrs': {'a': 'b'}}])) %}
      {% do out['columns_block'].append('execute immediate ' ~ icecore._sql(icecore._icecore_columns_signature_block(quoted), 'string')) %}
      {% do out['try_show'].append('execute immediate ' ~ icecore._sql(icecore._icecore_try_show_block('show columns in table ' ~ quoted), 'string')) %}
      {% do out['try_scalar'].append('execute immediate ' ~ icecore._sql(icecore._icecore_try_scalar_block('select count(*) from ' ~ quoted), 'string')) %}
      {% do out['guarded_ddl'].append('execute immediate ' ~ icecore._sql(icecore._icecore_guarded_ddl_block('create view ' ~ quoted ~ ' as select 1'), 'string')) %}
      {% do out['relation_quoted_name'].append('select * from ' ~ icecore._icecore_quoted_name(rel)) %}
      {% do out['schema_quoted_name'].append('select * from ' ~ icecore._icecore_schema_quoted_name(rel)) %}
      {% do out['changes_source'].append('select count(*) from ' ~ icecore._icecore_changes_source(rel, '2026-01-01')) %}
      {% do out['body'].append('CREATE TASK t AS EXECUTE IMMEDIATE $$ BEGIN SELECT * FROM ' ~ quoted ~ '; END; $$') %}
    {% else %}
      {% for name in names %}{% do out[name].append(none) %}{% endfor %}
    {% endif %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}

{#- The procedure name the hooks would CALL for the target database, and its guard block; {procedure, sql, error}. -#}
{% macro it_procedure_for_target() %}
  {% set built = icecore._icecore_procedure_or_error('ICECORE_EMIT') %}
  {% do _it_result({'procedure': built['procedure'], 'error': built['error'],
      'sql': icecore._icecore_guarded_call_sql(built['procedure'], [{'name': 'x', 'attrs': {}}]) if built['procedure'] else none}) %}
{% endmacro %}
