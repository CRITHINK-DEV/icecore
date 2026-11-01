{#- Test wrappers for the end_run row counts the cost estimate reads (tests/test_end_run_row_counts.py). -#}

{#- The counts for n nodes, as the estimate reads them. -#}
{% macro it_end_run_row_counts(nodes, query_events_per_node=1) %}
  {% do _it_result(icecore._icecore_end_run_row_counts(nodes, query_events_per_node)) %}
{% endmacro %}

{#- The rows end_run really writes for these results: records (spans and logs) plus the events of the spans. -#}
{% macro it_end_run_rows_written(results) %}
  {% set records = icecore._icecore_emit_run_records(icecore._icecore_end_run_items(results)) %}
  {% set out = {'spans': 0, 'span_events': 0, 'logs': 0} %}
  {% for record in records %}
    {% if record.get('type') == 'log' %}
      {% do out.update({'logs': out['logs'] + 1}) %}
    {% else %}
      {% do out.update({'spans': out['spans'] + 1, 'span_events': out['span_events'] + (record.get('events') or []) | length}) %}
    {% endif %}
  {% endfor %}
  {% do out.update({'rows': out['spans'] + out['span_events'] + out['logs']}) %}
  {% do _it_result(out) %}
{% endmacro %}
