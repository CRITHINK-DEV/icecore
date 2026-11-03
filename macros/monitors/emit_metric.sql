{#- UC2 METRIC emitter: the one way a monitor writes a metric (spec section 4; spikes/GATE1.md, option B).

    emit_metric(relation, metric, value, column, dimension, anomaly) writes through icecore.emit_events, in one call:
      - one span event `icecore.metric`: dbt.metric.name, dbt.metric.value (always a number), dbt.metric.column and
        dbt.metric.dimension when given, dbt.node.relation (the quoted "DB"."SCHEMA"."NAME") and, only when an
        anomaly result is given, its dbt.anomaly.* keys (engine, expected, lower, upper, score, is_anomaly);
      - when that result says is_anomaly, a second span event `icecore.anomaly` at level warn, with the same keys
        and a message. Gate 1 lets ICECORE_EMIT write span events only (LOG records come from ICECORE_EMIT_RUN at
        on-run-end), so the anomaly record is this event. Both go in the same CALL.
    A value that is not a number writes nothing and logs a warning. Never fails the run: emit_events guards the CALL.

    A monitor calls it once per metric it computed, then passes the scored result as `anomaly`:
      {%- set value = icecore.get_row_count_metadata(model) -%}
      {%- set result = icecore.score(history_sql, value) -%}
      {%- do icecore.emit_metric(model, 'row_count', value, anomaly=result) -%} -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{% macro emit_metric(relation, metric, value, column=none, dimension=none, anomaly=none, attributes=none, threshold=none) %}
  {{ return(icecore.emit_events(icecore._icecore_metric_events(relation, metric, value, column, dimension, anomaly, attributes, threshold))) }}
{% endmacro %}


{#- Several metrics of one relation in one emit_events call, so in one CALL (up to 100 events), for a monitor that
    computes many values in one run. metrics: [{metric, value, column, dimension, anomaly, attributes, threshold}], the keys as
    emit_metric's arguments (only metric and value are required). Same rules as emit_metric for each entry. -#}
{% macro emit_metrics(relation, metrics) %}
  {%- set events = [] -%}
  {%- for m in metrics -%}
    {%- do events.extend(icecore._icecore_metric_events(relation, m['metric'], m['value'], m.get('column'), m.get('dimension'), m.get('anomaly'), m.get('attributes'), m.get('threshold'))) -%}
  {%- endfor -%}
  {{ return(icecore.emit_events(events)) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The events emit_metric sends, as emit_events takes them ({kind, message, attributes, level}); [] when value is
    not a number. -#}
{% macro _icecore_metric_events(relation, metric, value, column=none, dimension=none, anomaly=none, attributes=none, threshold=none) %}
  {%- set name = relation if relation is string else icecore._icecore_quoted_name(relation) -%}
  {%- set number = icecore._icecore_metric_number(value) -%}
  {%- if number is none -%}
    {%- do log('icecore: warning: emit_metric: ' ~ metric ~ ' on ' ~ name ~ ' is not written: its value ('
        ~ value ~ ') is not a number', info=True) -%}
    {{ return([]) }}
  {%- endif -%}
  {%- set attrs = {'dbt.metric.name': metric | string, 'dbt.metric.value': number, 'dbt.node.relation': name} -%}
  {%- do icecore._icecore_put(attrs, 'dbt.metric.column', none if column is none else column | string) -%}
  {%- do icecore._icecore_put(attrs, 'dbt.metric.dimension', none if dimension is none else dimension | string) -%}
  {#- the limit the test judges the metric by (freshness: max_lag in seconds), as `icecore.metric.threshold`; not a number: left out -#}
  {%- do icecore._icecore_put(attrs, 'icecore.metric.threshold', icecore._icecore_metric_number(threshold)) -%}
  {%- for key, extra in (attributes or {}).items() -%}
    {%- if (key | string)[:11] == 'dbt.window.' -%}
      {%- do icecore._icecore_put(attrs, key, extra) -%}
    {%- else -%}
      {%- do log('icecore: warning: emit_metric: attribute ' ~ key ~ ' is ignored: only dbt.window.* keys are accepted', info=True) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set get = icecore._icecore_get -%}
  {%- if anomaly is not none -%}
    {%- do icecore._icecore_put(attrs, 'dbt.anomaly.engine', none if get(anomaly, 'engine') is none else get(anomaly, 'engine') | string) -%}
    {%- for key in ['expected', 'lower', 'upper', 'score'] -%}
      {%- do icecore._icecore_put(attrs, 'dbt.anomaly.' ~ key, icecore._icecore_metric_number(get(anomaly, key))) -%}
    {%- endfor -%}
    {%- set flag = get(anomaly, 'is_anomaly') -%}
    {%- do icecore._icecore_put(attrs, 'dbt.anomaly.is_anomaly', flag if flag is boolean else none) -%}
  {%- endif -%}
  {%- set events = [{'kind': 'metric', 'attributes': attrs, 'level': 'info'}] -%}
  {%- if attrs.get('dbt.anomaly.is_anomaly') is true -%}
    {%- do events.append({'kind': 'anomaly', 'attributes': attrs, 'level': 'warn', 'message': 'icecore: anomaly: '
        ~ metric ~ (' of ' ~ column if column is not none else '') ~ (' [' ~ dimension ~ ']' if dimension is not none else '')
        ~ ' on ' ~ name ~ ' = ' ~ number ~ ', expected ' ~ attrs.get('dbt.anomaly.expected') ~ ' in ['
        ~ attrs.get('dbt.anomaly.lower') ~ ', ' ~ attrs.get('dbt.anomaly.upper') ~ ']'}) -%}
  {%- endif -%}
  {{ return(events) }}
{% endmacro %}


{#- A metric value as a JSON number: int or float, never a string, a boolean, NaN or infinity. Read through its text
    so that a Decimal from run_query (which tojson cannot write) becomes an int or a float too. -#}
{% macro _icecore_metric_number(value) %}
  {%- if value is none or value is boolean -%}
    {{ return(none) }}
  {%- endif -%}
  {{ return(icecore._icecore_number(value | string)) }}
{% endmacro %}
