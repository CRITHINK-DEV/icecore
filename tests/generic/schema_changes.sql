{#- icecore.schema_changes: zero-scan schema drift test (UC2). Documented in macros/tests/schema_changes.yml (on dbt 1.8, a generic test's docs are read only from macro-paths).
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
      {%- set previous = icecore._icecore_previous_signature(model) -%}
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
      {#- The full column state, every run: one `schema_signature` record per chunk of columns (a wide table is split
          so no record reaches the payload cap and no column is lost). The chunk index and count let the baseline read
          and the schema_columns view put the chunks back together. -#}
      {%- set overhead = (tojson(icecore.base_attributes()) | length) + (tojson({'dbt.node.relation': name, 'dbt.schema.columns': '',
          'dbt.schema.chunk_index': 99999, 'dbt.schema.chunk_count': 99999, 'icecore.level': 'info', 'icecore.message': ''}) | length) + 128 -%}
      {%- set chunks = icecore._icecore_signature_chunks(current, none, overhead) -%}
      {%- set events = [] -%}
      {%- for chunk in chunks -%}
        {%- do events.append({'kind': 'schema_signature', 'level': 'info',
            'attributes': {'dbt.node.relation': name, 'dbt.schema.columns': tojson(chunk),
                           'dbt.schema.chunk_index': loop.index0, 'dbt.schema.chunk_count': chunks | length}}) -%}
      {%- endfor -%}
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
