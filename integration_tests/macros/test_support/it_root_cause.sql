{#- Test wrappers for the UC4 root-cause macros. Results are logged as `icecore_it_result:<json>` for pytest. -#}

{% macro it_classify_cause(node, run_results=[], signals={}) %}
  {% do log('icecore_it_result:' ~ tojson({'kind': icecore.classify_cause(node, run_results, signals)}), info=True) %}
{% endmacro %}

{% macro it_walk_upstream(unique_id, results=[], signals={}) %}
  {% do log('icecore_it_result:' ~ tojson({'cause': icecore.walk_upstream(unique_id, results, signals)}), info=True) %}
{% endmacro %}

{#- emit_root_cause on given results and signals (no query): logs the batch items it annotated or appended. -#}
{% macro it_emit_root_cause(results, items=[], signals=none) %}
  {% set batch = {'items': items, 'node_spans': {}} %}
  {% do icecore.emit_root_cause(results, batch, signals) %}
  {% do log('icecore_it_result:' ~ tojson(batch['items']), info=True) %}
{% endmacro %}

{#- What a schema_changes monitor emits: one span event `icecore.schema_change` per change of diff_schema(previous,
    current), keyed as the SCHEMA_CHANGES view reads it. -#}
{% macro it_emit_schema_diff(previous, current, unique_id, relation) %}
  {% set diff = icecore.diff_schema(previous, current) %}
  {% set events = [] %}
  {% for kind, key in [('added', 'added'), ('removed', 'removed'), ('type_changed', 'type_changed')] %}
    {% for c in diff[key] %}
      {% do events.append({'kind': 'schema_change', 'level': 'warn', 'attributes': {
          'dbt.node.unique_id': unique_id, 'dbt.node.relation': relation, 'dbt.schema.change_kind': kind,
          'dbt.schema.column': c['name'], 'dbt.schema.from_type': c.get('from_type') or (c.get('data_type') if kind == 'removed' else none),
          'dbt.schema.to_type': (c.get('to_type') or c.get('data_type')) if kind != 'removed' else none}}) %}
    {% endfor %}
  {% endfor %}
  {% do log('icecore_it_result:' ~ tojson(icecore.emit_events(events)), info=True) %}
{% endmacro %}
