{#- UC5 telemetry levels (spikes/GATE1.md, "Prerequisites" and open item 7; spec section 2, "Snowflake edition").

    Levels belong to the schema that holds the ICECORE_EMIT procedures, never to the session or the account, and not to
    the whole database: ALTER DATABASE ... TRACE_LEVEL = 'ALWAYS' would trace every procedure and UDF the client has
    there. Measured on 1 Nov 2026 (S12 levels matrix, spikes/results/gate1_followups.json): a level set on the schema
    (or on the procedures) takes over from the database's, and LOG_LEVEL INFO with TRACE_LEVEL ALWAYS on the schema lands
    every record of ICECORE_EMIT and ICECORE_EMIT_RUN. TRACE_LEVEL ON_EVENT is not enough: ICECORE_EMIT_RUN writes
    explicit spans, and under ON_EVENT only a span that carries an event lands. So the needs are LOG_LEVEL at least
    INFO and TRACE_LEVEL ALWAYS, effective on the schema (set there or inherited from the database).

    The only statement icecore may run is ALTER SCHEMA <db>.<schema> SET LOG_LEVEL / TRACE_LEVEL, and only inside a
    guard: it needs MODIFY LOG LEVEL and MODIFY TRACE LEVEL on the account, so on most accounts the role lacks the
    privilege, the statement is printed for an admin and the run goes on. Everything else is printed, never run:
    ALTER ACCOUNT (needs ACCOUNTADMIN), the database's EVENT_TABLE association, CREATE EVENT TABLE (the package creates no
    tables) and the recommended clustering key. Database-level event tables need Enterprise; on Standard the account's
    active table is used. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Check the levels against the detection result (and `schema_levels`, what icecore.schema_levels read on the obs schema) and act on the plan. Returns
    {database, levels_ok, executed: [...], printed: [...], notes: [...]} and logs every printed statement. -#}
{% macro set_telemetry_levels(capabilities, database=none, dedicated_event_table=none, apply=true, schema=none, schema_levels=none) %}
  {%- set p = icecore.telemetry_levels_plan(capabilities, database, dedicated_event_table, schema, schema_levels) -%}
  {%- set executed = [] -%}
  {%- set printed = [] -%}
  {%- set notes = [] -%}
  {%- for statement in p['attempt'] -%}
    {%- if apply and execute -%}
      {%- set status = icecore._icecore_guarded_ddl(statement) -%}
      {%- if status == 'ok' -%}
        {%- do executed.append(statement) -%}
      {%- else -%}
        {%- do printed.append(statement) -%}
        {%- do notes.append('could not set the levels with role ' ~ target.role ~ ' (' ~ status ~ '); an admin must run the statement') -%}
      {%- endif -%}
    {%- else -%}
      {%- do printed.append(statement) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- do printed.extend(p['print']) -%}
  {%- do notes.extend(p['notes']) -%}

  {%- for statement in executed -%}
    {% do log('icecore: ran: ' ~ statement, info=True) %}
  {%- endfor -%}
  {%- if p['levels_ok'] -%}
    {% do log('icecore: telemetry levels of ' ~ p['database'] ~ '.' ~ p['schema'] ~ ' are sufficient (LOG_LEVEL ' ~ p['log_level'] ~ ', TRACE_LEVEL ' ~ p['trace_level'] ~ '): nothing changed', info=True) %}
  {%- endif -%}
  {%- for note in notes -%}
    {% do log('icecore: ' ~ note, info=True) %}
  {%- endfor -%}
  {%- if printed -%}
    {% do log('icecore: statements for an admin to run (icecore does not run them):\n' ~ (printed | join(';\n')) ~ ';', info=True) %}
  {%- endif -%}
  {{ return({'database': p['database'], 'levels_ok': p['levels_ok'], 'executed': executed, 'printed': printed, 'notes': notes}) }}
{% endmacro %}


{#- Pure: what to do for a detection result, with no query. Returns
    {database, log_level, trace_level, levels_ok, attempt: [...], print: [...], notes: [...]}.
    `attempt` only ever holds the obs schema's ALTER SCHEMA ... SET LOG_LEVEL / TRACE_LEVEL. The levels checked are
    `schema_levels` ({log_level, trace_level} effective on the schema) when given, else the database's from
    `capabilities`. -#}
{% macro telemetry_levels_plan(capabilities, database=none, dedicated_event_table=none, schema=none, schema_levels=none) %}
  {%- if capabilities is none or capabilities is not mapping -%}
    {{ exceptions.raise_compiler_error("icecore: set_telemetry_levels needs the result of icecore.detect_capabilities() as `capabilities`") }}
  {%- endif -%}
  {%- set database = database or capabilities.get('database') or target.database -%}
  {%- set db = icecore._icecore_identifier(database) -%}
  {%- set edition = (capabilities.get('edition') or 'UNKNOWN') | upper -%}
  {%- set active = (capabilities.get('event_table') or '') | trim -%}
  {%- set schema_name = icecore.obs_schema_name(schema) -%}
  {%- set effective = schema_levels if schema_levels is mapping else capabilities -%}
  {%- set log_level = (effective.get('log_level') or 'OFF') | upper -%}
  {%- set trace_level = (effective.get('trace_level') or 'OFF') | upper -%}
  {%- set attempt = [] -%}
  {%- set to_print = [] -%}
  {%- set notes = [] -%}
  {%- set cluster = ' CLUSTER BY (TO_DATE(TIMESTAMP), RECORD_TYPE)' -%}

  {#- Event table -#}
  {%- if dedicated_event_table and edition == 'STANDARD' -%}
    {%- do notes.append('a database-level event table needs Enterprise edition; on Standard, icecore uses the account\'s active event table (' ~ (active or 'none') ~ ')') -%}
  {%- elif dedicated_event_table -%}
    {%- if active | upper != dedicated_event_table | trim | upper -%}
      {%- if edition not in ('ENTERPRISE', 'BUSINESS_CRITICAL', 'VIRTUAL_PRIVATE_SNOWFLAKE') -%}
        {%- do notes.append('edition ' ~ edition ~ ': a database-level event table needs Enterprise edition') -%}
      {%- endif -%}
      {%- do to_print.append('CREATE EVENT TABLE IF NOT EXISTS ' ~ dedicated_event_table) -%}
      {%- do to_print.append('ALTER TABLE ' ~ dedicated_event_table ~ cluster) -%}
      {%- do to_print.append('ALTER DATABASE ' ~ db ~ ' SET EVENT_TABLE = ' ~ dedicated_event_table) -%}
    {%- endif -%}
  {%- elif active == '' -%}
    {%- do to_print.append('ALTER ACCOUNT SET EVENT_TABLE = SNOWFLAKE.TELEMETRY.EVENTS') -%}
    {%- do notes.append('no active event table: records are not collected until ACCOUNTADMIN activates one') -%}
  {%- elif active | upper == 'SNOWFLAKE.TELEMETRY.EVENTS' -%}
    {%- do notes.append('the views read SNOWFLAKE.TELEMETRY.EVENTS_VIEW: their readers need the SNOWFLAKE.EVENTS_VIEWER application role') -%}
  {%- else -%}
    {%- do to_print.append('ALTER TABLE ' ~ active ~ cluster) -%}
    {%- do notes.append('recommended: cluster the event table ' ~ active ~ ' on (TO_DATE(TIMESTAMP), RECORD_TYPE) so icecore views prune') -%}
  {%- endif -%}

  {#- Levels of the obs schema: LOG_LEVEL at least INFO, TRACE_LEVEL ALWAYS (ON_EVENT loses spans without events) -#}
  {%- set sets = [] -%}
  {%- if log_level not in ('INFO', 'DEBUG', 'TRACE') -%}{%- do sets.append("LOG_LEVEL = 'INFO'") -%}{%- endif -%}
  {%- if trace_level != 'ALWAYS' -%}{%- do sets.append("TRACE_LEVEL = 'ALWAYS'") -%}{%- endif -%}
  {%- if sets -%}
    {%- do attempt.append('ALTER SCHEMA ' ~ db ~ '.' ~ icecore._icecore_identifier(schema_name) ~ ' SET ' ~ (sets | join(', '))) -%}
  {%- endif -%}
  {%- if trace_level == 'ON_EVENT' -%}
    {%- do notes.append('TRACE_LEVEL ON_EVENT loses the explicit spans that ICECORE_EMIT_RUN writes without an event (measured, spikes/GATE1.md item 7): icecore needs ALWAYS on its schema') -%}
  {%- endif -%}
  {{ return({'database': database, 'schema': schema_name, 'log_level': log_level, 'trace_level': trace_level, 'levels_ok': not sets,
             'attempt': attempt, 'print': to_print, 'notes': notes}) }}
{% endmacro %}


{#- {log_level, trace_level} effective on `schema` (its own value or the database's, SHOW PARAMETERS IN SCHEMA), or none when
    the schema cannot be read (it does not exist yet, or the role has no USAGE). -#}
{% macro schema_levels(database, schema=none) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set shown = icecore._icecore_try_show("show parameters like '%LEVEL' in schema " ~ icecore._icecore_identifier(database) ~ '.'
      ~ icecore._icecore_identifier(icecore.obs_schema_name(schema))) -%}
  {%- if 'error' in shown -%}{{ return(none) }}{%- endif -%}
  {%- set params = {} -%}
  {%- for row in shown.get('rows') or [] -%}
    {%- do params.update({row['key'] | upper: (row['value'] or '') | upper}) -%}
  {%- endfor -%}
  {%- if 'LOG_LEVEL' not in params or 'TRACE_LEVEL' not in params -%}{{ return(none) }}{%- endif -%}
  {{ return({'log_level': params['LOG_LEVEL'], 'trace_level': params['TRACE_LEVEL']}) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- One DDL statement inside a Snowflake Scripting block that catches every error: 'ok' or
    'icecore-error <code>: <message>', never raises. -#}
{% macro _icecore_guarded_ddl(statement) %}
  {%- set block -%}
begin
  execute immediate {{ icecore._icecore_sql_literal(statement) }};
  return 'ok';
exception
  when other then return 'icecore-error ' || sqlcode || ': ' || sqlerrm;
end;
  {%- endset -%}
  {{ return(run_query('execute immediate ' ~ icecore._icecore_sql_literal(block)).rows[0][0]) }}
{% endmacro %}
