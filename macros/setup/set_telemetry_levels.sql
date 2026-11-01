{#- UC5 telemetry levels (spikes/GATE1.md, "Prerequisites"; spec section 2, "Snowflake edition").

    Levels belong to the target database, never to the session or the account: LOG_LEVEL at least INFO and TRACE_LEVEL
    ON_EVENT or ALWAYS. The ICECORE_EMIT procedures inherit them, and with them their records land whatever the session's
    levels are (S1.md, proc.levels_off).

    The only statement icecore may run is ALTER DATABASE <db> SET LOG_LEVEL / TRACE_LEVEL, and only inside a guard: when
    the role lacks the privilege, the statement is printed for an admin and the run goes on. Everything else is printed,
    never run: ALTER ACCOUNT (needs ACCOUNTADMIN), the database's EVENT_TABLE association, CREATE EVENT TABLE (the
    package creates no tables) and the recommended clustering key. Database-level event tables need Enterprise; on
    Standard the account's active table is used. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Check the levels against the detection result and act on the plan. Returns
    {database, levels_ok, executed: [...], printed: [...], notes: [...]} and logs every printed statement. -#}
{% macro set_telemetry_levels(capabilities, database=none, dedicated_event_table=none, apply=true) %}
  {%- set p = icecore.telemetry_levels_plan(capabilities, database, dedicated_event_table) -%}
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
    {% do log('icecore: telemetry levels of ' ~ p['database'] ~ ' are sufficient (LOG_LEVEL ' ~ p['log_level'] ~ ', TRACE_LEVEL ' ~ p['trace_level'] ~ '): nothing changed', info=True) %}
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
    `attempt` only ever holds the database's ALTER DATABASE ... SET LOG_LEVEL / TRACE_LEVEL. -#}
{% macro telemetry_levels_plan(capabilities, database=none, dedicated_event_table=none) %}
  {%- if capabilities is none or capabilities is not mapping -%}
    {{ exceptions.raise_compiler_error("icecore: set_telemetry_levels needs the result of icecore.detect_capabilities() as `capabilities`") }}
  {%- endif -%}
  {%- set database = database or capabilities.get('database') or target.database -%}
  {%- set db = icecore._icecore_identifier(database) -%}
  {%- set edition = (capabilities.get('edition') or 'UNKNOWN') | upper -%}
  {%- set active = (capabilities.get('event_table') or '') | trim -%}
  {%- set log_level = (capabilities.get('log_level') or 'OFF') | upper -%}
  {%- set trace_level = (capabilities.get('trace_level') or 'OFF') | upper -%}
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

  {#- Levels of the database -#}
  {%- set sets = [] -%}
  {%- if log_level not in ('INFO', 'DEBUG', 'TRACE') -%}{%- do sets.append("LOG_LEVEL = 'INFO'") -%}{%- endif -%}
  {%- if trace_level not in ('ON_EVENT', 'ALWAYS') -%}{%- do sets.append("TRACE_LEVEL = 'ALWAYS'") -%}{%- endif -%}
  {%- if sets -%}
    {%- do attempt.append('ALTER DATABASE ' ~ db ~ ' SET ' ~ (sets | join(', '))) -%}
  {%- endif -%}
  {{ return({'database': database, 'log_level': log_level, 'trace_level': trace_level, 'levels_ok': not sets,
             'attempt': attempt, 'print': to_print, 'notes': notes}) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- One DDL statement inside a Snowflake Scripting block that catches every error: 'ok' or
    'icecore-error <code>: <message>', never raises. -#}
{% macro _icecore_guarded_ddl(statement) %}
  {{ return(run_query('execute immediate ' ~ icecore._sql(icecore._icecore_guarded_ddl_block(statement), 'string')).columns[0].values()[0]) }}
{% endmacro %}


{#- The block of _icecore_guarded_ddl: the statement travels as a literal built with icecore._sql. -#}
{% macro _icecore_guarded_ddl_block(statement) %}
  {%- set block -%}
begin
  execute immediate {{ icecore._sql(statement, 'string') }};
  return 'ok';
exception
  when other then return 'icecore-error ' || sqlcode || ': ' || sqlerrm;
end;
  {%- endset -%}
  {{ return(block) }}
{% endmacro %}
