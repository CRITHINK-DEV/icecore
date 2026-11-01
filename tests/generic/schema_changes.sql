{#- icecore.schema_changes: zero-scan schema drift test (UC2). Documented in schema_changes.yml.
    The current columns come from icecore.get_columns_signature (SHOW COLUMNS, no data scan). The baseline is the
    signature the previous run wrote as an `icecore.schema_signature` record, read back through
    icecore.base_events() for this relation. The diff (icecore.diff_schema) is emitted as one
    `icecore.schema_change` record per change, plus the new signature, in one CALL. Every failing row is one change.
    With no baseline (first run) or no readable history the test passes and only records the signature.
    Pass or fail comes only from the diff: the baseline read and the emit are guarded. -#}
{% test schema_changes(model) %}
  {%- set changes = [] -%}
  {%- if execute -%}
    {%- set name = icecore._icecore_quoted_name(model) -%}
    {%- set current = icecore.get_columns_signature(model) -%}
    {%- if current is none -%}
      {%- do log('icecore: warning: schema_changes of ' ~ name ~ ' could not be measured: its columns are not readable; the test passes', info=True) -%}
    {%- else -%}
      {#- baseline: the newest signature record of this relation; any failure here means "no baseline" -#}
      {%- set previous = none -%}
      {%- set parameter_rows = run_query("show parameters like 'EVENT_TABLE' in database " ~ icecore._icecore_identifier(target.database)).rows -%}
      {%- set event_table = icecore.event_table_to_read(parameter_rows[0]['value'] if parameter_rows | length > 0 else none) -%}
      {%- if event_table is not none -%}
        {%- set baseline_sql -%}
          select attributes:"dbt.schema.columns"::string
          from ({{ icecore.base_events(event_table=event_table) }})
          where record_name = 'icecore.schema_signature' and node_relation = {{ icecore._icecore_sql_literal(name) }}
          order by event_at desc
          limit 1
        {%- endset -%}
        {%- set raw = icecore._icecore_safe_scalar(baseline_sql, 'schema_changes baseline', model) -%}
        {%- set previous = fromjson(raw) if raw is string else none -%}
      {%- endif -%}
      {%- set diff = icecore.diff_schema(previous, current) -%}
      {%- for c in diff['added'] -%}
        {%- do changes.append({'kind': 'added', 'column': c['name'], 'from_type': none, 'to_type': c['data_type']}) -%}
      {%- endfor -%}
      {%- for c in diff['removed'] -%}
        {%- do changes.append({'kind': 'removed', 'column': c['name'], 'from_type': c['data_type'], 'to_type': none}) -%}
      {%- endfor -%}
      {%- for c in diff['type_changed'] -%}
        {%- do changes.append({'kind': 'type_changed', 'column': c['name'], 'from_type': c['from_type'], 'to_type': c['to_type']}) -%}
      {%- endfor -%}
      {%- set events = [{'kind': 'schema_signature', 'level': 'info',
          'attributes': {'dbt.node.relation': name, 'dbt.schema.columns': tojson(current)}}] -%}
      {%- for ch in changes -%}
        {%- set attrs = {'dbt.node.relation': name, 'dbt.schema.change_kind': ch['kind'], 'dbt.schema.column': ch['column']} -%}
        {%- if ch['from_type'] is not none -%}{%- do attrs.update({'dbt.schema.from_type': ch['from_type']}) -%}{%- endif -%}
        {%- if ch['to_type'] is not none -%}{%- do attrs.update({'dbt.schema.to_type': ch['to_type']}) -%}{%- endif -%}
        {%- do events.append({'kind': 'schema_change', 'level': 'warn', 'attributes': attrs,
            'message': 'icecore: schema change on ' ~ name ~ ': ' ~ ch['kind'] ~ ' column ' ~ ch['column']
                ~ (' from ' ~ ch['from_type'] if ch['from_type'] is not none else '')
                ~ (' to ' ~ ch['to_type'] if ch['to_type'] is not none else '')}) -%}
      {%- endfor -%}
      {#- UC3 scoring hook: schema changes are not scored; nothing plugs in here. -#}
      {%- do icecore.emit_events(events) -%}
    {%- endif -%}
  {%- endif -%}

  {%- if changes -%}
    {%- for ch in changes %}
  select {{ icecore._icecore_sql_literal(ch['kind']) }}::varchar as change_kind,
         {{ icecore._icecore_sql_literal(ch['column']) }}::varchar as column_name,
         {{ 'null' if ch['from_type'] is none else icecore._icecore_sql_literal(ch['from_type']) }}::varchar as from_type,
         {{ 'null' if ch['to_type'] is none else icecore._icecore_sql_literal(ch['to_type']) }}::varchar as to_type
  {{ 'union all' if not loop.last }}
    {%- endfor -%}
  {%- else %}
  select null::varchar as change_kind, null::varchar as column_name, null::varchar as from_type, null::varchar as to_type
  where false
  {%- endif %}
{% endtest %}
