{#- UC5 capabilities (spec sections 2 and 5; spikes/GATE1.md). What the account supports, detected with reads
    only, each inside a guard so that no read can fail the run:

    - edition: SNOWFLAKE.ORGANIZATION_USAGE.ACCOUNTS for the current account; when that view cannot be read, a
      database with an event table of its own means Enterprise or above (database-level event tables need
      Enterprise), otherwise UNKNOWN;
    - the database's EVENT_TABLE (and the level it is set at), LOG_LEVEL and TRACE_LEVEL: SHOW PARAMETERS IN DATABASE;
    - Cortex anomaly detection: the class SNOWFLAKE.ML.ANOMALY_DETECTION visible to the role (SHOW CLASSES). Any error,
      a missing privilege included, means false with the reason.

    The result is recorded in-band as one span event `icecore.capabilities` through icecore.emit (UC1), with
    icecore.capability.* keys, and the vars block for dbt_project.yml is printed. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- Returns {database, edition, edition_source, event_table, event_table_level, log_level, trace_level, cortex,
    cortex_reason, recorded}. `recorded` is emit's result ({events, calls, statuses}), or none with record=false. -#}
{% macro detect_capabilities(record=true, database=none, cortex_class='SNOWFLAKE.ML.ANOMALY_DETECTION') %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set database = (database or target.database) | string -%}
  {%- set database = database | upper if modules.re.match('^[A-Za-z_][A-Za-z0-9_$]*$', database) else database -%}

  {%- set params = {} -%}
  {%- set shown = icecore._icecore_try_show("show parameters in database " ~ icecore._icecore_identifier(database)) -%}
  {%- for row in shown.get('rows') or [] -%}
    {%- do params.update({row['key'] | upper: row}) -%}
  {%- endfor -%}
  {%- if 'error' in shown -%}
    {% do log('icecore: warning: could not read the parameters of database ' ~ database ~ ': ' ~ shown['error'], info=True) %}
  {%- endif -%}
  {%- set event_table = params.get('EVENT_TABLE', {}).get('value') or '' -%}
  {%- set event_table_level = (params.get('EVENT_TABLE', {}).get('level') or '') | upper -%}

  {%- set org = icecore._icecore_try_scalar("select edition from snowflake.organization_usage.accounts where account_locator = current_account() limit 1") -%}
  {%- set edition = icecore._icecore_edition(org.get('value'), event_table_level) -%}

  {%- set cortex = icecore._icecore_cortex(cortex_class) -%}

  {%- set capabilities = {
      'database': database,
      'edition': edition[0],
      'edition_source': edition[1],
      'event_table': event_table,
      'event_table_level': event_table_level,
      'log_level': (params.get('LOG_LEVEL', {}).get('value') or '') | upper,
      'trace_level': (params.get('TRACE_LEVEL', {}).get('value') or '') | upper,
      'cortex': cortex[0],
      'cortex_reason': cortex[1],
  } -%}

  {% do log('icecore: capabilities of ' ~ database ~ ': edition ' ~ capabilities['edition'] ~ ' (' ~ capabilities['edition_source'] ~ '), event table '
      ~ (event_table or 'none') ~ (' (set at ' ~ event_table_level ~ ' level)' if event_table_level else '') ~ ', LOG_LEVEL ' ~ (capabilities['log_level'] or 'unknown')
      ~ ', TRACE_LEVEL ' ~ (capabilities['trace_level'] or 'unknown') ~ ', Cortex ' ~ ('yes' if cortex[0] else 'no (' ~ cortex[1] ~ ')'), info=True) %}
  {% do log('icecore: flags for dbt_project.yml:\n' ~ icecore.capabilities_vars_block(capabilities), info=True) %}

  {%- do capabilities.update({'recorded': icecore._icecore_record_capabilities(capabilities) if record else none}) -%}
  {{ return(capabilities) }}
{% endmacro %}


{#- The vars block for dbt_project.yml that matches a detection result. -#}
{% macro capabilities_vars_block(capabilities) %}
  {%- set cortex = capabilities.get('cortex') is sameas true -%}
  {%- set lines = [
      'vars:',
      '  icecore:',
      '    obs_schema: ' ~ (var('icecore', {}).get('obs_schema', 'ICECORE_OBS') | string | upper),
      '    anomaly_engine: ' ~ ('auto      # Cortex anomaly detection is available' if cortex else 'native    # Cortex anomaly detection is not available to this role'),
      '    shadow_engine: none' ~ ('       # set to cortex to compare engines on evidence' if cortex else ''),
  ] -%}
  {{ return(lines | join('\n')) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- The one call site that records the capabilities: a span event `icecore.capabilities` through icecore.emit. -#}
{% macro _icecore_record_capabilities(capabilities) %}
  {{ return(icecore.emit('capabilities', 'capabilities detected', icecore._icecore_capability_attributes(capabilities))) }}
{% endmacro %}


{% macro _icecore_capability_attributes(capabilities) %}
  {%- set out = {} -%}
  {%- for key in ['database', 'edition', 'edition_source', 'event_table', 'event_table_level', 'log_level', 'trace_level', 'cortex', 'cortex_reason'] -%}
    {%- do out.update({'icecore.capability.' ~ key: capabilities.get(key)}) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- (edition, source): the organization's answer first; else a database-level event table means Enterprise. -#}
{% macro _icecore_edition(org_edition, event_table_level) %}
  {%- if org_edition -%}{{ return([org_edition | string | upper, 'organization_usage']) }}{%- endif -%}
  {%- if (event_table_level or '') | upper == 'DATABASE' -%}{{ return(['ENTERPRISE', 'database_event_table']) }}{%- endif -%}
  {{ return(['UNKNOWN', 'unknown']) }}
{% endmacro %}


{#- (available, reason): the class is visible to the role. -#}
{% macro _icecore_cortex(cortex_class) %}
  {%- set parts = cortex_class.split('.') -%}
  {%- set shown = icecore._icecore_try_show("show classes like " ~ icecore._icecore_sql_literal(parts[-1]) ~ " in schema " ~ (parts[:-1] | join('.'))) -%}
  {%- if 'error' in shown -%}{{ return([false, shown['error']]) }}{%- endif -%}
  {%- if not shown['rows'] -%}{{ return([false, cortex_class ~ ' is not available in this account or region']) }}{%- endif -%}
  {{ return([true, none]) }}
{% endmacro %}


{#- Run a SHOW inside a Snowflake Scripting block that catches every error: {'rows': [{column: value}]} or
    {'error': '<code>: <message>'}, never raises. -#}
{% macro _icecore_try_show(show_sql) %}
  {%- set block -%}
declare
  res variant;
begin
  {{ show_sql }};
  select coalesce(array_agg(object_construct(*)), array_construct()) into :res from table(result_scan(last_query_id()));
  return object_construct('rows', res);
exception
  when other then return object_construct('error', sqlcode || ': ' || sqlerrm);
end;
  {%- endset -%}
  {{ return(fromjson(run_query('execute immediate ' ~ icecore._icecore_sql_literal(block)).rows[0][0])) }}
{% endmacro %}
