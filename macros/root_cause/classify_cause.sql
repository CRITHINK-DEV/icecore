{#-
  classify_cause(node, run_results, signals) -> 'upstream_failure' | 'schema_change' | 'stale_source' | 'none'

  Says why one node could be the cause of a downstream test failure. Pure: it runs no
  query, so it can be rendered and tested without Snowflake. Signals are passed in;
  wiring them to the views comes later.

  Precedence when several apply: upstream_failure (the node itself failed in this run),
  then schema_change, then stale_source. A skipped node is not a cause: it was skipped
  because of something further up, which the walk will reach.
-#}
{% macro classify_cause(node, run_results=[], signals={}) %}
  {% set unique_id = icecore._root_cause_unique_id(node) %}
  {% set signals = signals or {} %}
  {% set status = icecore._root_cause_status_map(run_results).get(unique_id) %}

  {% if status is not none and (status | string | lower) in ['error', 'fail', 'runtime error'] %}
    {{ return('upstream_failure') }}
  {% elif (signals.get('schema_diffs') or {}).get(unique_id) %}
    {{ return('schema_change') }}
  {% elif (signals.get('freshness_breaches') or {}).get(unique_id) %}
    {{ return('stale_source') }}
  {% endif %}
  {{ return('none') }}
{% endmacro %}


{#- A graph node (mapping with unique_id) or a unique_id string -> the unique_id. -#}
{% macro _root_cause_unique_id(node) %}
  {% if node is string %}
    {{ return(node) }}
  {% elif node is mapping %}
    {{ return(node.get('unique_id')) }}
  {% endif %}
  {{ return(node.unique_id) }}
{% endmacro %}


{#-
  Run results -> {unique_id: status}. Accepts dbt's on-run-end `results` (objects with
  .node.unique_id and .status) and plain mappings, either {"unique_id", "status"} or
  {"node": {"unique_id"}, "status"}.
-#}
{% macro _root_cause_status_map(run_results) %}
  {% set statuses = {} %}
  {% for result in (run_results or []) %}
    {% if result is mapping %}
      {% set uid = result.get('unique_id') or (result.get('node') or {}).get('unique_id') %}
      {% set status = result.get('status') %}
    {% else %}
      {% set uid = result.node.unique_id %}
      {% set status = result.status %}
    {% endif %}
    {% if uid %}
      {% do statuses.update({uid: status}) %}
    {% endif %}
  {% endfor %}
  {{ return(statuses) }}
{% endmacro %}
