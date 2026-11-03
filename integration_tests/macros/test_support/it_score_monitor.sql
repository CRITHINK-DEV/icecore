{#- Test wrappers for icecore.score_monitor_metrics (macros/monitors/score_monitor.sql), tests/test_score_monitor.py.
    Results are logged as `icecore_it_result:<json>`; each stubbed query logs `icecore: stub query <n> point(s)`. -#}

{#- Offline stand-in for the one guarded scoring query. The case is var it_monitor_case: quiet (inside the band), anomaly
    (all outside), first_anomaly (point 0 outside), short (3 points, insufficient_history), young (as quiet: 10 days, under Cortex's 14), error. -#}
{% macro _it_monitor_runner(sql) %}
  {%- set indexes = modules.re.findall('select (\\d+) as i, object_construct', sql) -%}
  {%- do log('icecore: stub query ' ~ (indexes | length) ~ ' point(s)', info=True) -%}
  {%- set case = var('it_monitor_case', 'quiet') -%}
  {%- if case == 'error' -%}{{ return({'error': 'forced scoring failure'}) }}{%- endif -%}
  {%- set out = {} -%}
  {%- for i in indexes -%}
    {%- set outside = case == 'anomaly' or (case == 'first_anomaly' and i == '0') -%}
    {%- do out.update({i: {'ENGINE': 'native', 'EXPECTED': 100.0, 'LOWER': 90.0, 'UPPER': 110.0,
                           'SCORE': 890.0 if outside else 0.0, 'IS_ANOMALY': outside,
                           'BASELINE': 'insufficient_history' if case == 'short' else 'flat',
                           'HISTORY_POINTS': 3 if case == 'short' else 30, 'HISTORY_DAYS': 3 if case == 'short' else 10}}) -%}
  {%- endfor -%}
  {{ return({'value': out}) }}
{% endmacro %}

{% macro it_score_monitor(rel, points, kwargs={}) %}
  {% set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) %}
  {% set results = icecore.score_monitor_metrics(relation, points, runner=_it_monitor_runner, **kwargs) %}
  {% set events = [] %}
  {% for p in points %}
    {% do events.extend(icecore._icecore_metric_events(relation, p['metric'], p['value'], p.get('column'), p.get('dimension'), results[loop.index0])) %}
  {% endfor %}
  {% do _it_result({'results': results, 'events': events}) %}
{% endmacro %}

{#- The statement the stub would receive, for the first point (history window and key). -#}
{% macro it_score_monitor_sql(rel, point) %}
  {% set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) %}
  {% do _it_result(icecore.get_metric_history_sql(icecore._icecore_quoted_name(relation), point['metric'], point.get('column'), point.get('dimension'), 30, 1)) %}
{% endmacro %}
