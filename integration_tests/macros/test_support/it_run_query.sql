{#- Test-only stand-in for dbt's run_query, active only with --vars '{it_query_stub: true}': it logs every query
    (`icecore: query: <what>`) and answers from a script instead of Snowflake, so a test can count the queries a
    macro renders and run it offline. Without the var it is dbt's own run_query, unchanged. Used by
    tests/test_cortex_engine.py (query counts of the dispatcher). -#}
{% macro run_query(sql) %}
  {%- if not var('it_query_stub', false) -%}
    {{ return(dbt.run_query(sql)) }}
  {%- endif -%}
  {%- set text = sql | lower -%}
  {%- if 'drop procedure' in text -%}
    {%- if var('it_query_stub_forbid_drop', false) -%}
      {{ exceptions.raise_compiler_error('it stub: a DROP was issued: ' ~ sql) }}
    {%- endif -%}
    {%- do log('icecore: query: drop', info=True) -%}
    {{ return({'rows': [[var('it_drop_status', 'ok')]]}) }}
  {%- elif 'create or replace procedure' in text -%}
    {%- do log('icecore: query: create procedure', info=True) -%}
    {{ return({'rows': [['ok']]}) }}
  {%- elif 'datediff(\'day\'' in text -%}
    {%- do log('icecore: query: history', info=True) -%}
    {{ return({'rows': [[30]]}) }}
  {%- elif 'icecore_score' in text -%}
    {%- do log('icecore: query: score call', info=True) -%}
    {{ return({'rows': [[tojson({'state': 'ok', 'age_days': 1.0, 'rows': [{'Y': 500, 'FORECAST': 100, 'LOWER_BOUND': 90, 'UPPER_BOUND': 110, 'IS_ANOMALY': true}]})]]}) }}
  {%- endif -%}
  {%- do log('icecore: query: other', info=True) -%}
  {{ return({'rows': [[tojson({'error': 'not scripted'})]]}) }}
{% endmacro %}
