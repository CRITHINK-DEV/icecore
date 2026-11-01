{#- Test wrappers for the icecore views (macros/views/). Results are logged as `icecore_it_result:<json>`. -#}

{% macro it_create_views(schema=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.create_views(schema)), info=True) %}
{% endmacro %}

{#- Renders without running: the list of views, each {name, sql}. -#}
{% macro it_views_render(schema=none, event_table='DB.TELEMETRY.EVENTS') %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_views(schema, event_table)), info=True) %}
{% endmacro %}

{% macro it_view_ddl(view, sql, schema=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_view_ddl(view, sql, schema)), info=True) %}
{% endmacro %}

{#- Metric history (macros/engines/get_metric_history.sql). `rel` is a quoted name string or {database, schema, identifier}. -#}
{% macro it_metric_history(rel, metric, kwargs={}) %}
  {% set relation = rel if rel is string else it_relation(rel['database'], rel['schema'], rel['identifier'], true) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.get_metric_history(relation, metric, **kwargs)), info=True) %}
{% endmacro %}

{#- Reads the points get_metric_history's sql returns, then feeds that sql to icecore.score. -#}
{% macro it_metric_history_scored(rel, metric, current_value, kwargs={}) %}
  {% set history = icecore.get_metric_history(rel, metric, **kwargs) %}
  {% set rows = [] %}
  {% for r in run_query(history['sql']).rows %}{% do rows.append([r[0] | string, r[1] | float]) %}{% endfor %}
  {% set scored = icecore.score(history['sql'], current_value, engine='native') %}
  {% do log('icecore_it_result:' ~ tojson({'history': history, 'rows': rows, 'scored': scored}), info=True) %}
{% endmacro %}

{% macro it_history_delay(delay_minutes=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_history_delay(delay_minutes)), info=True) %}
{% endmacro %}

{#- UC6 silence (macros/control/). `entries` are collect_sla_config entries. -#}
{% macro it_silence_task_sql(entries, schema=none, source=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.render_silence_task_sql(entries, schema, source)), info=True) %}
{% endmacro %}

{% macro it_silence_task_ddl(body, schema=none, warehouse='WH', schedule='5 MINUTE') %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_silence_task_ddl(icecore._icecore_procedure('ICECORE_SILENCE', schema), body, warehouse, schedule)), info=True) %}
{% endmacro %}

{% macro it_silence_procedure_ddl(schema=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore._icecore_silence_procedure_ddl(schema)), info=True) %}
{% endmacro %}

{% macro it_create_silence_task(schema=none, entries=none, source=none, resume=none) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.create_silence_task(schema, entries, source, none, none, resume)), info=True) %}
{% endmacro %}
