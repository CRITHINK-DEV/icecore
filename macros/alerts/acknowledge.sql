{#- UC6 acknowledge: `dbt run-operation icecore.acknowledge --args '{incident_key: <key>, by: <who>}'`. Records that a
    person has seen the incident: one span event `icecore.ack` through icecore.emit (ICECORE_EMIT; never an INSERT),
    attributes icecore.ack.incident_key and icecore.ack.by. The alerts_feed view then shows acknowledged_at and
    acknowledged_by on that incident_key; a recurrence in a later window has a new key and alerts again. The app calls
    the same macro. `by` defaults to the target's user. Returns emit's result {events, calls, statuses}. -#}
{% macro acknowledge(incident_key, by=none) %}
  {%- if not incident_key or (incident_key | string | trim) == '' -%}
    {{ exceptions.raise_compiler_error('icecore.acknowledge needs an incident_key (a value of alerts_feed.incident_key)') }}
  {%- endif -%}
  {%- set who = (by or target.user or 'unknown') | string -%}
  {%- set key = incident_key | string | trim -%}
  {%- set out = icecore.emit('ack', 'incident ' ~ key ~ ' acknowledged by ' ~ who,
      {'icecore.ack.incident_key': key, 'icecore.ack.by': who}) -%}
  {%- do log('icecore: acknowledged incident ' ~ key ~ ' by ' ~ who, info=True) -%}
  {{ return(out) }}
{% endmacro %}
