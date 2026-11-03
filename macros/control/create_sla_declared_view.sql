{#- UC7 SLA_DECLARED view: every model that declares meta.sla, one row each, whether or not it was ever silent
    (SILENCE_EVENTS holds verdicts only). Created by setup's `create_tasks` step from the same collect_sla_config
    entries and the same escaped JSON literal (icecore._sql over tojson) as the silence task body, so meta.sla
    text is data: it can only appear inside that one single-quoted literal. With no meta.sla the view is still created,
    with zero rows, so the app can tell "none declared" from "not set up". The SLA is embedded when setup runs.
    Columns: unique_id, expected_every_minutes, grace_minutes, channel, severity. -#}


{#- The CREATE VIEW statement. Only entries with meta.sla are included. -#}
{% macro sla_declared_view_ddl(entries, schema=none) %}
  {%- set declared = [] -%}
  {%- for e in entries or [] -%}
    {%- if e['expected_every_minutes'] is not none -%}
      {%- do declared.append({'unique_id': e['unique_id'], 'expected_every_minutes': e['expected_every_minutes'] | int,
                              'grace_minutes': e['grace_minutes'] | int, 'channel': e['channel'],
                              'severity': e['severity'] or 'warn'}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return('CREATE OR REPLACE VIEW ' ~ icecore._icecore_procedure('SLA_DECLARED', schema) ~ ' COPY GRANTS AS\n'
      ~ 'select f.value:unique_id::string as unique_id, f.value:expected_every_minutes::number as expected_every_minutes, '
      ~ 'f.value:grace_minutes::number as grace_minutes, f.value:channel::string as channel, '
      ~ 'f.value:severity::string as severity\n'
      ~ 'from table(flatten(input => parse_json(' ~ icecore._sql(tojson(declared), 'string') ~ '))) f') }}
{% endmacro %}


{#- Creates the view; returns the guarded status ('ok' or the error). Not run at parse time. -#}
{% macro create_sla_declared_view(schema=none, entries=none) %}
  {%- if not execute -%}{{ return('skipped') }}{%- endif -%}
  {%- set entries = icecore.collect_sla_config() if entries is none else entries -%}
  {%- set status = icecore._icecore_guarded_ddl(icecore.sla_declared_view_ddl(entries, schema)) -%}
  {%- if status != 'ok' -%}
    {%- do log('icecore: warning: could not create SLA_DECLARED (' ~ status ~ ')', info=True) -%}
  {%- endif -%}
  {{ return(status) }}
{% endmacro %}
