{#- UC5 doctor (spec sections 3 and 10; spikes/GATE1.md, "What setup creates and the grants it needs").

    dbt run-operation icecore.doctor [--args '{schema: ICECORE_OBS}']

    Checks, in order, and reports; it never fails the run (every read and CALL is guarded):
      1. event_table       re-detects the capabilities (detect_capabilities, reads only): an active event table;
      2. levels            the database's LOG_LEVEL at least INFO and TRACE_LEVEL ON_EVENT or ALWAYS
                           (telemetry_levels_plan); the fix is printed, never run;
      3. ICECORE_EMIT, ICECORE_EMIT_RUN
                           one guarded test CALL of each, tagged icecore.doctor.probe = true (an
                           icecore.doctor_probe span event and an INFO LOG record): a missing procedure or grant
                           shows here;
      4. records           icecore records of the last 24 hours, counted through base_events() on Snowflake's clock,
                           without doctor's probes and the icecore.capabilities records of setup and doctor.
    The likely cause of "no records in 24 h" is the first failed check of 1-3, else the telemetry hooks.
    Then the capabilities are recorded through ICECORE_EMIT, as setup does. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns _icecore_doctor_report(...) plus {database, schema, probes, capabilities, recorded}. -#}
{% macro doctor(schema=none) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set schema = icecore.obs_schema_name(schema) -%}
  {%- set capabilities = icecore.detect_capabilities(record=false) -%}
  {%- set probes = icecore._icecore_doctor_probes(schema) -%}

  {%- set count = none -%}
  {%- set count_error = none -%}
  {%- set event_table = icecore.event_table_to_read(capabilities['event_table']) -%}
  {%- if event_table is not none -%}
    {%- set read = icecore._icecore_try_scalar(icecore._icecore_doctor_count_sql(event_table, capabilities['database'], schema)) -%}
    {%- if 'error' in read -%}
      {%- set count_error = read['error'] -%}
    {%- else -%}
      {%- set count = read.get('value') | int -%}
    {%- endif -%}
  {%- endif -%}

  {%- set report = icecore._icecore_doctor_report(capabilities, probes, count, count_error, schema) -%}
  {%- for check in report['checks'] -%}
    {% do log('icecore: doctor: check ' ~ check['name'] ~ ': ' ~ ('ok' if check['ok'] else 'FAILED') ~ ' (' ~ check['detail'] ~ ')', info=True) %}
  {%- endfor -%}
  {% do log('icecore: doctor: ' ~ report['message'], info=True) %}

  {%- set recorded = icecore._icecore_emit_in_schema(schema, 'capabilities', 'capabilities detected by doctor',
      icecore._icecore_capability_attributes(capabilities)) if probes['ICECORE_EMIT'].startswith('ok') else none -%}
  {%- do report.update({'database': capabilities['database'], 'schema': schema, 'probes': probes,
                        'capabilities': capabilities, 'recorded': recorded}) -%}
  {{ return(report) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- One guarded test CALL of each procedure of `schema`: {ICECORE_EMIT: status, ICECORE_EMIT_RUN: status}. -#}
{% macro _icecore_doctor_probes(schema) %}
  {%- set tag = {'icecore.doctor.probe': true} -%}
  {%- set log_attrs = {} -%}
  {%- do log_attrs.update(icecore.base_attributes()) -%}
  {%- do log_attrs.update(tag) -%}
  {{ return({
      'ICECORE_EMIT': icecore._icecore_emit_in_schema(schema, 'doctor_probe', 'icecore doctor probe', tag) | string,
      'ICECORE_EMIT_RUN': icecore._icecore_guarded_call(icecore._icecore_procedure('ICECORE_EMIT_RUN', schema),
          [{'type': 'log', 'level': 'info', 'message': 'icecore doctor probe', 'attrs': log_attrs}]) | string,
  }) }}
{% endmacro %}


{#- The count of icecore records of the last 24 hours (Snowflake's clock; TIMESTAMP is UTC), without doctor's
    probes and the capabilities records. -#}
{% macro _icecore_doctor_count_sql(event_table, database, schema) %}
  {%- set sql -%}
select count(*) from (
{{ icecore.base_events(event_table=event_table, database=database, obs_schema=icecore.obs_schema_name(schema)) }}
) as icecore_events
where event_at >= dateadd(hour, -24, convert_timezone('UTC', current_timestamp())::timestamp_ntz)
  and not coalesce(try_to_boolean(attributes:"icecore.doctor.probe"::string), false)
  and coalesce(record_name, '') <> 'icecore.capabilities'
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- Pure: the checks and the verdict. status: ok (every check passed, records found), no_records (cause:
    event_table, levels, procedures or hooks), or warn (records found but a check failed, or the count could not be
    read). Returns {status, cause, message, records_24h, checks: [{name, ok, detail}]}. -#}
{% macro _icecore_doctor_report(capabilities, probes, count, count_error=none, schema=none) %}
  {%- set where = (capabilities.get('database') or target.database) ~ '.' ~ icecore.obs_schema_name(schema) -%}
  {%- set checks = [] -%}
  {%- set causes = {} -%}

  {%- set event_table = (capabilities.get('event_table') or '') | trim -%}
  {%- do checks.append({'name': 'event_table', 'ok': event_table != '',
      'detail': event_table or 'no active event table; an admin must set one (icecore.set_telemetry_levels prints the statement)'}) -%}

  {%- set plan = icecore.telemetry_levels_plan(capabilities) -%}
  {%- set levels = 'LOG_LEVEL ' ~ plan['log_level'] ~ ', TRACE_LEVEL ' ~ plan['trace_level'] -%}
  {%- do checks.append({'name': 'levels', 'ok': plan['levels_ok'], 'detail': levels if plan['levels_ok'] else levels
      ~ '; icecore needs LOG_LEVEL at least INFO and TRACE_LEVEL ON_EVENT or ALWAYS on the database; an admin can run: '
      ~ (plan['attempt'] | join('; '))}) -%}

  {%- for name in ['ICECORE_EMIT', 'ICECORE_EMIT_RUN'] -%}
    {%- set status = (probes or {}).get(name) or 'not called' -%}
    {%- set ok = status.startswith('ok') -%}
    {%- do checks.append({'name': name, 'ok': ok, 'detail': 'test CALL: ' ~ status if ok else 'test CALL: ' ~ status
        ~ '; run icecore.setup, and grant USAGE on the database, the schema and the procedure to the role that runs dbt'}) -%}
  {%- endfor -%}

  {%- if count_error -%}
    {%- set records_detail = 'could not count icecore records: ' ~ count_error -%}
  {%- elif count is none -%}
    {%- set records_detail = 'not counted: no event table to read' -%}
  {%- else -%}
    {%- set records_detail = count ~ ' icecore records in the last 24 h in ' ~ where -%}
  {%- endif -%}
  {%- do checks.append({'name': 'records', 'ok': count is not none and count > 0, 'detail': records_detail}) -%}

  {%- set failed = [] -%}
  {%- for check in checks if not check['ok'] and check['name'] != 'records' -%}
    {%- do failed.append(check) -%}
  {%- endfor -%}

  {%- if count == 0 or (count is none and not count_error and failed) -%}
    {%- set first = failed[0]['name'] if failed else 'hooks' -%}
    {%- set cause = 'procedures' if first in ('ICECORE_EMIT', 'ICECORE_EMIT_RUN') else first -%}
    {%- if cause == 'hooks' -%}
      {%- set why = 'the telemetry hooks: check that dbt_project.yml has on-run-start: icecore.start_run() and on-run-end: icecore.end_run(results), '
          ~ 'and that a dbt command has run since (on dbt 1.8, source freshness fires them only with flags: source_freshness_run_project_hooks: true)' -%}
    {%- else -%}
      {%- set why = failed[0]['name'] ~ ': ' ~ failed[0]['detail'] -%}
    {%- endif -%}
    {%- set out = {'status': 'no_records', 'cause': cause, 'message': 'no records in 24 h in ' ~ where ~ '; likely cause: ' ~ why} -%}
  {%- elif count is none -%}
    {%- set out = {'status': 'warn', 'cause': 'read', 'message': records_detail} -%}
  {%- elif failed -%}
    {%- set problems = [] -%}
    {%- for check in failed -%}{%- do problems.append(check['name'] ~ ': ' ~ check['detail']) -%}{%- endfor -%}
    {%- set out = {'status': 'warn', 'cause': failed[0]['name'], 'message': records_detail ~ ', but ' ~ (problems | join('; '))} -%}
  {%- else -%}
    {%- set out = {'status': 'ok', 'cause': none, 'message': 'OK: ' ~ records_detail} -%}
  {%- endif -%}
  {%- do out.update({'records_24h': count, 'checks': checks}) -%}
  {{ return(out) }}
{% endmacro %}
