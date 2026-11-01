{#- Test wrappers for the failed-row sample emitter (tests/test_samples.py). Results are logged as
    `icecore_it_result:<json>`. `results` are run-result dicts shaped like run_results.json, each with its node. -#}

{#- The unique ids of the results emit_samples would query, and the row limit it would use. -#}
{% macro it_sample_candidates(results) %}
  {% set ids = [] %}
  {% for candidate in icecore._icecore_sample_candidates(results) %}
    {% do ids.append(candidate['unique_id']) %}
  {% endfor %}
  {% do _it_result({'ids': ids, 'limit': icecore._icecore_sample_limit()}) %}
{% endmacro %}

{#- The query for a failures relation, as it is sent. -#}
{% macro it_sample_sql(relation_name, limit) %}
  {% do _it_result(icecore._icecore_sample_sql(relation_name, limit)) %}
{% endmacro %}

{#- The sample LOG for a result and the rows the query returned, as ICECORE_EMIT_RUN receives it. -#}
{% macro it_sample_render(result, rows) %}
  {% set record = icecore._icecore_sample_log(result, rows) %}
  {% do _it_result(icecore._icecore_emit_run_records([record])[0]) %}
{% endmacro %}
