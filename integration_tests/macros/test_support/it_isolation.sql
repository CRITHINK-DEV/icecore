{#- Test wrappers for failure isolation (tests/test_failure_isolation.py). Results are logged as
    `icecore_it_result:<json>`. The emitters below are stand-ins for the end-of-run emitters of
    icecore._icecore_end_run_emitters(): the test passes them to _icecore_end_run_items as extra_emitters. -#}

{#- An emitter that reports an error the way an emitter whose guarded query failed does. -#}
{% macro it_emitter_error(results, batch) %}
  {{ return({'error': 'icecore-error 2141: simulated'}) }}
{% endmacro %}

{#- An emitter that adds one marker LOG, tagged with var it_run_id, to prove it ran. -#}
{% macro it_emitter_marker(results, batch) %}
  {% do batch['items'].append({'type': 'log', 'level': 'info', 'message': 'it marker',
      'attrs': {'icecore.kind': 'it_marker', 'it.run_id': var('it_run_id', 'none')}}) %}
{% endmacro %}

{#- An emitter whose guarded query fails: a CALL of a procedure that does not exist. Needs a connection. -#}
{% macro it_emitter_guarded_failure(results, batch) %}
  {% set status = icecore._icecore_guarded_call(icecore._icecore_procedure('ICECORE_EMIT', 'ICECORE_OBS_MISSING_' ~ var('it_run_id', 'x') | upper), []) %}
  {{ return({'error': status}) }}
{% endmacro %}

{% macro _it_emitters(names) %}
  {% set table = {'error': it_emitter_error, 'marker': it_emitter_marker, 'guarded_failure': it_emitter_guarded_failure} %}
  {% set out = [] %}
  {% for name in names %}
    {% if name == 'not_callable' %}
      {% do out.append({'name': 'not_callable', 'var': 'node_results', 'default': true, 'macro': 'nope'}) %}
    {% elif name == 'malformed' %}
      {% do out.append('not a mapping') %}
    {% else %}
      {% do out.append({'name': 'it_' ~ name, 'var': 'it_' ~ name, 'default': true, 'macro': table[name]}) %}
    {% endif %}
  {% endfor %}
  {{ return(out) }}
{% endmacro %}

{#- end_run's items with these extra emitters after the package's own, rendered, nothing sent. -#}
{% macro it_end_run_isolation(results, emitters) %}
  {% set items = icecore._icecore_end_run_items(results, _it_emitters(emitters)) %}
  {% do _it_result({'items': items}) %}
{% endmacro %}

{#- end_run's items with these emitters, sent with emit_run: what end_run does. Needs a connection. -#}
{% macro it_end_run_isolation_send(results, emitters) %}
  {% do _it_result(icecore.emit_run(icecore._icecore_end_run_items(results, _it_emitters(emitters)))) %}
{% endmacro %}

{#- One guarded CALL of any procedure (fully qualified, as written) with these items. -#}
{% macro it_guarded_call(procedure, items) %}
  {% do _it_result(icecore._icecore_guarded_call(procedure, items)) %}
{% endmacro %}

{#- A guarded call's status and the warning count, for the offline checks that never reach a connection. -#}
{% macro it_chunk_literals(events, max_bytes) %}
  {% set records = icecore._icecore_emit_records(events) %}
  {% set out = [] %}
  {% for chunk in icecore._icecore_emit_chunks(records, 100, max_bytes) %}
    {% do out.append({'records': chunk | length, 'literal_bytes': icecore._icecore_block_literal(tojson(chunk)) | length}) %}
  {% endfor %}
  {% do _it_result(out) %}
{% endmacro %}
