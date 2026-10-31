{#-
  walk_upstream(unique_id, results, signals) -> {"unique_id", "kind", "distance"} | none

  Breadth-first over depends_on in the dbt graph, starting from the parents of
  `unique_id` (the failed test or node itself is never its own cause). Returns the
  nearest ancestor that classify_cause flags. Among flagged nodes at the same distance,
  the kind precedence of classify_cause decides (upstream_failure, schema_change,
  stale_source), then depends_on order. Each node is visited once, so a diamond is walked
  once. Runs no query. Returns none when nothing upstream is flagged, when unique_id is
  not in the graph, or at parse time.
-#}
{% macro walk_upstream(unique_id, results=[], signals={}) %}
  {% if not execute %}
    {{ return(none) }}
  {% endif %}

  {% set start = icecore._root_cause_graph_node(unique_id) %}
  {% if start is none %}
    {{ return(none) }}
  {% endif %}

  {#- Built once, then shared by every classify_cause call. -#}
  {% set statuses = icecore._root_cause_status_map(results) %}
  {% set rank = icecore._root_cause_kind_rank() %}
  {% set seen = {unique_id: true} %}
  {% set state = {'frontier': icecore._root_cause_parents(start), 'found': none} %}
  {#- One pass per level; a graph has at most as many levels as nodes. -#}
  {% for distance in range(1, (graph.nodes | length) + (graph.sources | length) + 2) %}
    {% if state.frontier and state.found is none %}
      {% set next_level = [] %}
      {% for uid in state.frontier %}
        {% if uid not in seen %}
          {% do seen.update({uid: true}) %}
          {% set kind = icecore.classify_cause(uid, statuses, signals) %}
          {% if kind != 'none' %}
            {% if state.found is none or rank[kind] < rank[state.found.kind] %}
              {% do state.update({'found': {'unique_id': uid, 'kind': kind, 'distance': distance}}) %}
            {% endif %}
          {% else %}
            {% set node = icecore._root_cause_graph_node(uid) %}
            {% if node is not none %}
              {% do next_level.extend(icecore._root_cause_parents(node)) %}
            {% endif %}
          {% endif %}
        {% endif %}
      {% endfor %}
      {% do state.update({'frontier': next_level}) %}
    {% endif %}
  {% endfor %}
  {{ return(state.found) }}
{% endmacro %}


{#- unique_id -> the node or source from the dbt graph, or none. -#}
{% macro _root_cause_graph_node(unique_id) %}
  {{ return(graph.nodes.get(unique_id) or graph.sources.get(unique_id)) }}
{% endmacro %}


{#- A graph node -> the unique_ids it depends on (sources have none). -#}
{% macro _root_cause_parents(node) %}
  {{ return(((node.get('depends_on') or {}).get('nodes')) or []) }}
{% endmacro %}
