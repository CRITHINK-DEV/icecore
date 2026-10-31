{#- UC5 base event filter: the one SELECT every icecore view starts from (spikes/GATE1.md, "Identity and record
    shape" and "Consequences for the build").

    Predicates come first, in this order, so the filter sits directly on the event-table scan:
      1. RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG'), the only types icecore writes;
      2. RESOURCE_ATTRIBUTES snow.database.name = the target database, snow.schema.name = the icecore schema
         (var icecore.obs_schema, default ICECORE_OBS) and snow.executable.name like 'ICECORE_EMIT%': the records
         of icecore's two procedures in this environment only, so environments sharing one table stay apart;
      3. the icecore identity: SCOPE icecore (ICECORE_EMIT_RUN records), or a SPAN_EVENT named icecore.*
         (ICECORE_EMIT records, whose SCOPE is global because SQL cannot set it).
    Native dbt Projects records fail this filter by design; their own predicate is still to be defined (GATE1.md,
    open item 8).

    Attributes are typed with TRY_TO_*: a malformed value reads as null and never fails a view.
    observed_at is OBSERVED_TIMESTAMP, which is NULL on every ICECORE_EMIT span event (spike S3); use event_at there. -#}


{#- Public ----------------------------------------------------------------------------------------- -#}

{#- SQL text of the filtered, typed SELECT; use it as a CTE body: `with events as ({{ icecore.base_events() }})`.
    Without event_table, the table is resolved with resolve_event_table(database) when the macro executes. -#}
{% macro base_events(event_table=none, database=none, obs_schema=none) %}
  {%- set database = database or target.database -%}
  {%- set obs_schema = obs_schema or var('icecore', {}).get('obs_schema', 'ICECORE_OBS') -%}
  {%- if event_table is none -%}
    {%- set event_table = icecore.resolve_event_table(database) if execute else 'SNOWFLAKE.TELEMETRY.EVENTS_VIEW' -%}
  {%- endif -%}
  {%- set columns = [] -%}
  {%- for column, key, kind in icecore._icecore_base_event_attributes() -%}
    {%- set path = 'RECORD_ATTRIBUTES:"' ~ key ~ '"::string' -%}
    {%- if kind == 'string' -%}
      {%- do columns.append(path ~ ' as ' ~ column) -%}
    {%- else -%}
      {%- do columns.append({'integer': 'try_to_number', 'float': 'try_to_double', 'boolean': 'try_to_boolean'}[kind] ~ '(' ~ path ~ ') as ' ~ column) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- set sql -%}
select
    TIMESTAMP as event_at,
    START_TIMESTAMP as started_at,
    OBSERVED_TIMESTAMP as observed_at,
    RECORD_TYPE as record_type,
    RECORD:name::string as record_name,
    RECORD:severity_text::string as severity,
    SCOPE:name::string as scope_name,
    TRACE:trace_id::string as trace_id,
    TRACE:span_id::string as span_id,
    RECORD:parent_span_id::string as parent_span_id,
    RESOURCE_ATTRIBUTES:"snow.executable.name"::string as executable_name,
    RESOURCE_ATTRIBUTES:"snow.query.id"::string as emit_query_id,
    {% for c in columns -%}
    {{ c }},
    {% endfor -%}
    VALUE as value,
    RECORD_ATTRIBUTES as attributes
from {{ event_table }}
where RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG')
  and RESOURCE_ATTRIBUTES:"snow.database.name"::string = {{ icecore._icecore_sql_literal(icecore._icecore_stored_name(database)) }}
  and RESOURCE_ATTRIBUTES:"snow.schema.name"::string = {{ icecore._icecore_sql_literal(icecore._icecore_stored_name(obs_schema)) }}
  and RESOURCE_ATTRIBUTES:"snow.executable.name"::string like 'ICECORE_EMIT%'
  and (
    SCOPE:name::string = 'icecore'
    or (RECORD_TYPE = 'SPAN_EVENT' and RECORD:name::string like 'icecore.%')
  )
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- The event table the views read for `database` (default: the target database), resolved once with
    SHOW PARAMETERS LIKE 'EVENT_TABLE' IN DATABASE (GATE1.md, "What setup creates"). Fails with a clear message
    when no event table is active, because no view could read anything. -#}
{% macro resolve_event_table(database=none) %}
  {%- if not execute -%}{{ return(none) }}{%- endif -%}
  {%- set database = database or target.database -%}
  {%- set rows = run_query("show parameters like 'EVENT_TABLE' in database " ~ icecore._icecore_identifier(database)).rows -%}
  {%- set value = rows[0]['value'] if rows | length > 0 else none -%}
  {%- set table = icecore.event_table_to_read(value) -%}
  {%- if table is none -%}
    {{ exceptions.raise_compiler_error("icecore: no active event table for database " ~ database ~ " (SHOW PARAMETERS LIKE 'EVENT_TABLE' IN DATABASE is empty); an admin must set one before icecore can read telemetry") }}
  {%- endif -%}
  {{ return(table) }}
{% endmacro %}


{#- Maps the EVENT_TABLE parameter to the relation the views read: the account table SNOWFLAKE.TELEMETRY.EVENTS is
    read through SNOWFLAKE.TELEMETRY.EVENTS_VIEW (readers need the EVENTS_VIEWER application role); any other table
    is read as it is. None or empty gives none. -#}
{% macro event_table_to_read(event_table) %}
  {%- if event_table is none or (event_table | trim) == '' -%}{{ return(none) }}{%- endif -%}
  {%- set name = event_table | trim -%}
  {%- if name | upper == 'SNOWFLAKE.TELEMETRY.EVENTS' -%}{{ return('SNOWFLAKE.TELEMETRY.EVENTS_VIEW') }}{%- endif -%}
  {{ return(name) }}
{% endmacro %}


{#- Private --------------------------------------------------------------------------------------- -#}

{#- (column, RECORD_ATTRIBUTES key, kind) for the typed columns; keys from spec section 4. -#}
{% macro _icecore_base_event_attributes() %}
  {{ return([
    ('invocation_id', 'dbt.invocation_id', 'string'),
    ('runtime', 'dbt.runtime', 'string'),
    ('command', 'dbt.command', 'string'),
    ('target_name', 'dbt.target', 'string'),
    ('project', 'dbt.project', 'string'),
    ('node_unique_id', 'dbt.node.unique_id', 'string'),
    ('node_type', 'dbt.node.type', 'string'),
    ('node_name', 'dbt.node.name', 'string'),
    ('node_materialization', 'dbt.node.materialization', 'string'),
    ('node_relation', 'dbt.node.relation', 'string'),
    ('node_query_id', 'snow.query.id', 'string'),
    ('result_status', 'dbt.result.status', 'string'),
    ('result_message', 'dbt.result.message', 'string'),
    ('result_rows_affected', 'dbt.result.rows_affected', 'integer'),
    ('result_execution_time_s', 'dbt.result.execution_time_s', 'float'),
    ('test_name', 'dbt.test.name', 'string'),
    ('test_type', 'dbt.test.type', 'string'),
    ('test_column', 'dbt.test.column', 'string'),
    ('test_severity', 'dbt.test.severity', 'string'),
    ('test_failures', 'dbt.test.failures', 'integer'),
    ('metric_name', 'dbt.metric.name', 'string'),
    ('metric_column', 'dbt.metric.column', 'string'),
    ('metric_dimension', 'dbt.metric.dimension', 'string'),
    ('metric_value', 'dbt.metric.value', 'float'),
    ('anomaly_engine', 'dbt.anomaly.engine', 'string'),
    ('anomaly_expected', 'dbt.anomaly.expected', 'float'),
    ('anomaly_lower', 'dbt.anomaly.lower', 'float'),
    ('anomaly_upper', 'dbt.anomaly.upper', 'float'),
    ('anomaly_score', 'dbt.anomaly.score', 'float'),
    ('anomaly_is_anomaly', 'dbt.anomaly.is_anomaly', 'boolean'),
    ('cost_credits', 'dbt.cost.credits', 'float'),
    ('cost_warehouse', 'dbt.cost.warehouse', 'string'),
    ('root_cause_unique_id', 'dbt.root_cause.unique_id', 'string'),
    ('root_cause_kind', 'dbt.root_cause.kind', 'string'),
    ('icecore_version', 'icecore.version', 'string'),
    ('icecore_schema_version', 'icecore.schema_version', 'integer'),
  ]) }}
{% endmacro %}


{#- The name Snowflake stores for an identifier as dbt hands it over: a plain identifier (unquoted on Snowflake)
    resolves to upper case; anything else is kept as written (a quoted name). -#}
{% macro _icecore_stored_name(name) %}
  {%- if modules.re.match('^[A-Za-z_][A-Za-z0-9_$]*$', name | string) -%}{{ return(name | string | upper) }}{%- endif -%}
  {{ return(name | string) }}
{% endmacro %}


{#- An identifier for SQL text: plain identifiers as they are, others double-quoted. -#}
{% macro _icecore_identifier(name) %}
  {%- if modules.re.match('^[A-Za-z_][A-Za-z0-9_$]*$', name | string) -%}{{ return(name | string) }}{%- endif -%}
  {{ return('"' ~ (name | string | replace('"', '""')) ~ '"') }}
{% endmacro %}
