{#-
  Spike S1, encoding E1: Snowflake Scripting (SYSTEM$LOG, SYSTEM$ADD_EVENT, SYSTEM$SET_SPAN_ATTRIBUTES).
  Probe code for the sandbox only, not package code.

  Every probe takes `via`:
    'anonymous'       EXECUTE IMMEDIATE $$ DECLARE <params with literal defaults> BEGIN ... END; $$
    'temp_procedure'  CREATE TEMPORARY PROCEDURE <target db>.<target schema>.S1_<KIND>_<run_id>(params)
                      with a static body, then CALL it with literal arguments, then DROP it.
  Values only ever reach SQL through s1_sql_literal.
-#}

{% macro s1_sql_literal(value) -%}
  {#- Single-quoted Snowflake string literal. Escapes the backslash first, then the quote.
      `$` becomes \x24 so a value can never close a $$ block; CR/LF are escaped to keep one line. -#}
  {%- set s = (value | string).replace('\\', '\\\\').replace("'", "\\'").replace('$', '\\x24') -%}
  {%- set s = s.replace('\n', '\\n').replace('\r', '\\r') -%}
  {{- return("'" ~ s ~ "'") -}}
{%- endmacro %}


{% macro _s1_attrs_json(run_id, attrs) -%}
  {%- set a = {} -%}
  {%- do a.update(attrs or {}) -%}
  {%- do a.update({'icecore.run_id': run_id}) -%}
  {{- return(tojson(a)) -}}
{%- endmacro %}


{% macro _s1_proc_name(kind, run_id) -%}
  {{- return(target.database ~ '.' ~ target.schema ~ '.S1_' ~ (kind | upper) ~ '_' ~ (run_id | upper)) -}}
{%- endmacro %}


{#- params: list of [name, type, literal]; decls: list of local declarations; body: statements.
    Returns {'setup': [...], 'call': sql, 'teardown': [...]}. -#}
{% macro _s1_sql_plan(kind, run_id, via, params, decls, body) %}
  {% if via == 'anonymous' %}
    {% set lines = [] %}
    {% for p in params %}{% do lines.append('  ' ~ p[0] ~ ' ' ~ p[1] ~ ' DEFAULT ' ~ p[2] ~ ';') %}{% endfor %}
    {% for d in decls %}{% do lines.append('  ' ~ d ~ ';') %}{% endfor %}
    {% set sql %}
EXECUTE IMMEDIATE $$
DECLARE
{{ lines | join('\n') }}
BEGIN
{{ body }}
END;
$$
    {% endset %}
    {{ return({'setup': [], 'call': sql, 'teardown': []}) }}
  {% elif via == 'temp_procedure' %}
    {% set name = _s1_proc_name(kind, run_id) %}
    {% set sig = [] %}{% set types = [] %}{% set args = [] %}
    {% for p in params %}
      {% do sig.append(p[0] ~ ' ' ~ p[1]) %}{% do types.append(p[1]) %}{% do args.append(p[2] | string) %}
    {% endfor %}
    {% set create %}
CREATE OR REPLACE TEMPORARY PROCEDURE {{ name }}({{ sig | join(', ') }})
RETURNS STRING
LANGUAGE SQL
AS $$
{% if decls %}DECLARE
{% for d in decls %}  {{ d }};
{% endfor %}{% endif -%}
BEGIN
{{ body }}
END;
$$
    {% endset %}
    {{ return({'setup': [create], 'call': 'CALL ' ~ name ~ '(' ~ args | join(', ') ~ ')',
               'teardown': ['DROP PROCEDURE IF EXISTS ' ~ name ~ '(' ~ types | join(', ') ~ ')']}) }}
  {% else %}
    {{ exceptions.raise_compiler_error("s1: unknown via '" ~ via ~ "'") }}
  {% endif %}
{% endmacro %}


{% macro _s1_run(plan) %}
  {% if execute %}
    {% for s in plan['setup'] %}{% do run_query(s) %}{% endfor %}
    {% do run_query(plan['call']) %}
    {% for s in plan['teardown'] %}{% do run_query(s) %}{% endfor %}
  {% endif %}
{% endmacro %}


{% macro _s1_log_plan(run_id, n, message, via) %}
  {% set body %}
  FOR i IN 1 TO n DO
    m := msg || ' ' || run_id || ' ' || i;
    SYSTEM$LOG_INFO(:m);
  END FOR;
  RETURN 'ok';
  {% endset %}
  {{ return(_s1_sql_plan('log', run_id, via,
      [['msg', 'STRING', s1_sql_literal(message)], ['run_id', 'STRING', s1_sql_literal(run_id)], ['n', 'INTEGER', n | int]],
      ['m STRING'], body)) }}
{% endmacro %}


{% macro _s1_event_plan(run_id, n, attrs, via) %}
  {% set body %}
  a := PARSE_JSON(a_json);
  FOR i IN 1 TO n DO
    SYSTEM$ADD_EVENT('icecore.probe', :a);
  END FOR;
  RETURN 'ok';
  {% endset %}
  {{ return(_s1_sql_plan('event', run_id, via,
      [['a_json', 'STRING', s1_sql_literal(_s1_attrs_json(run_id, attrs))], ['n', 'INTEGER', n | int]],
      ['a OBJECT'], body)) }}
{% endmacro %}


{% macro s1_sql_log(run_id, n, message='probe', via='anonymous') %}
  {% do _s1_run(_s1_log_plan(run_id, n, message, via)) %}
{% endmacro %}


{% macro s1_sql_event(run_id, n, attrs, via='anonymous') %}
  {% do _s1_run(_s1_event_plan(run_id, n, attrs, via)) %}
{% endmacro %}


{% macro s1_sql_span_attrs(run_id, attrs, via='anonymous') %}
  {% set body %}
  a := PARSE_JSON(a_json);
  SYSTEM$SET_SPAN_ATTRIBUTES(:a);
  RETURN 'ok';
  {% endset %}
  {% do _s1_run(_s1_sql_plan('span', run_id, via,
      [['a_json', 'STRING', s1_sql_literal(_s1_attrs_json(run_id, attrs))]], ['a OBJECT'], body)) %}
{% endmacro %}


{#- One span event and one log inside a transaction that is rolled back.
    Temporary procedures are created before BEGIN: CREATE is DDL and would commit the transaction. -#}
{% macro s1_in_rollback(run_id, via='anonymous') %}
  {% if execute %}
    {% set ev = _s1_event_plan(run_id, 1, {'icecore.rollback': true}, via) %}
    {% set lg = _s1_log_plan(run_id, 1, 'rollback', via) %}
    {% for s in ev['setup'] + lg['setup'] %}{% do run_query(s) %}{% endfor %}
    {% do run_query('BEGIN TRANSACTION') %}
    {% set before = run_query('SELECT CURRENT_TRANSACTION()').columns[0].values()[0] %}
    {% do run_query(ev['call']) %}
    {% do run_query(lg['call']) %}
    {% set after = run_query('SELECT CURRENT_TRANSACTION()').columns[0].values()[0] %}
    {% do run_query('ROLLBACK') %}
    {% for s in ev['teardown'] + lg['teardown'] %}{% do run_query(s) %}{% endfor %}
    {{ log('s1_in_rollback transaction before=' ~ before ~ ' after=' ~ after, info=True) }}
    {% if before is none or before != after %}
      {{ exceptions.raise_compiler_error('s1_in_rollback: the emission did not run inside one open transaction (before=' ~ before ~ ', after=' ~ after ~ ')') }}
    {% endif %}
  {% endif %}
{% endmacro %}


{#- Probes with LOG_LEVEL and TRACE_LEVEL OFF for the session. Must not raise. -#}
{% macro s1_levels_off(run_id, via='anonymous', probes=['sql_log', 'sql_event', 'sql_span_attrs', 'python']) %}
  {% if execute %}
    {% do run_query("ALTER SESSION SET LOG_LEVEL = 'OFF', TRACE_LEVEL = 'OFF'") %}
    {% if 'sql_log' in probes %}{% do s1_sql_log(run_id, 1, 'levels off', via) %}{% endif %}
    {% if 'sql_event' in probes %}{% do s1_sql_event(run_id, 1, {'icecore.levels': 'off'}, via) %}{% endif %}
    {% if 'sql_span_attrs' in probes %}{% do s1_sql_span_attrs(run_id, {'icecore.levels': 'off'}, via) %}{% endif %}
    {% if 'python' in probes %}{% do s1_python(run_id, 1, 1, {'icecore.levels': 'off'}, 'levels off', via) %}{% endif %}
  {% endif %}
{% endmacro %}
