{#- Test wrappers for icecore.emit_metric (macros/monitors/emit_metric.sql). Results are logged as
    `icecore_it_result:<json>` for pytest (tests/test_emit_metric.py). `rel` is {database, schema, identifier,
    quoted}, turned into a Relation with it_relation (it_metadata.sql). -#}

{#- Renders without running: the events emit_metric builds, the records and chunks emit_events would send. -#}
{% macro it_emit_metric_render(rel, metric, value, column=none, dimension=none, anomaly=none, attributes=none) %}
  {% set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) %}
  {% set events = icecore._icecore_metric_events(relation, metric, value, column, dimension, anomaly, attributes) %}
  {% set records = icecore._icecore_emit_records(events) %}
  {% set sizes = [] %}
  {% for chunk in icecore._icecore_emit_chunks(records) %}
    {% do sizes.append(chunk | length) %}
  {% endfor %}
  {% do log('icecore_it_result:' ~ tojson({'events': events, 'records': records, 'chunk_sizes': sizes}), info=True) %}
{% endmacro %}

{#- Runs emit_metric; logs what it returns ({events, calls, statuses}). -#}
{% macro it_emit_metric(rel, metric, value, column=none, dimension=none, anomaly=none) %}
  {% set relation = it_relation(rel['database'], rel['schema'], rel['identifier'], rel.get('quoted', false)) %}
  {% do log('icecore_it_result:' ~ tojson(icecore.emit_metric(relation, metric, value, column, dimension, anomaly)), info=True) %}
{% endmacro %}
