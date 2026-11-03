{#- UC1 end-of-run emitter `samples` (var icecore.samples, default false: opt-in, because samples copy row values): the failed rows of failed tests, as LOG
    records at INFO, `icecore.kind = sample`. For a test that failed (status fail; also warn when var
    samples_on_warn is true, default false) and whose failures dbt stored (config store_failures, so the node has
    a relation_name), it selects up to var sample_rows rows (default 10, clamped to 1..1000) of the failures
    relation through a guarded query (a Snowflake Scripting block that catches every error: a missing table or a
    privilege problem logs a warning and skips the sample, never fails the run). The record carries the test's
    node_attributes(node, result), `dbt.sample.rows` (the rows as a JSON list), `dbt.sample.row_count` and
    `dbt.sample.truncated`. Rows are dropped from the end until the record fits payload_cap_kb, and
    dbt.sample.truncated is then true. Columns named in var sample_exclude_columns (case-insensitive, default
    none) keep their name in each row and their value becomes '?', masked in the query itself so the value never leaves Snowflake. Samples hold real data from your tables: see the README, Configuration.
    Called by end_run as emit_samples(results, batch). -#}
{% macro emit_samples(results, batch) %}
  {%- set limit = icecore._icecore_sample_limit() -%}
  {%- set excluded = icecore._icecore_sample_excluded_columns() -%}
  {#- var sample_exclude_columns is project data and ends up in SQL: any name that is not plain [A-Za-z0-9_] fails
      closed, no sample is read (a sample without its mask would leak). -#}
  {%- set invalid = [] -%}
  {%- for name in excluded if not icecore._icecore_sample_valid_name(name) -%}
    {%- do invalid.append(name) -%}
  {%- endfor -%}
  {%- if invalid -%}
    {% do log('icecore: warning: samples skipped: sample_exclude_columns must hold plain column names (letters, digits, underscore); invalid: ' ~ (invalid | map('string') | map('truncate', 40) | join(', ')), info=True) %}
    {{ return(none) }}
  {%- endif -%}
  {%- for candidate in icecore._icecore_sample_candidates(results) -%}
    {%- if not icecore._icecore_sample_valid_relation(candidate['relation_name']) -%}
      {% do log('icecore: warning: samples skipped ' ~ candidate['unique_id'] ~ ': its failures relation name is not a plain or quoted identifier', info=True) %}
      {%- continue -%}
    {%- endif -%}
    {%- set out = icecore._icecore_try_scalar(icecore._icecore_sample_sql(candidate['relation_name'], limit, excluded)) -%}
    {%- if 'error' in out -%}
      {% do log('icecore: warning: samples could not read ' ~ candidate['relation_name'] ~ ' for ' ~ candidate['unique_id'] ~ ': ' ~ out['error'], info=True) %}
    {%- else -%}
      {%- do batch['items'].append(icecore._icecore_sample_log(candidate['result'], out.get('value') or [])) -%}
    {%- endif -%}
  {%- endfor -%}
{% endmacro %}


{#- var sample_rows: a whole number, default 10, clamped to 1..1000. -#}
{% macro _icecore_sample_limit() %}
  {%- set n = icecore._icecore_number(icecore._icecore_setting('sample_rows', 10)) -%}
  {%- set n = 10 if n is none or n is boolean else n | int -%}
  {{ return([[n, 1] | max, 1000] | min) }}
{% endmacro %}


{#- The results to sample: [{unique_id, relation_name, result}]. -#}
{% macro _icecore_sample_candidates(results) %}
  {%- set get = icecore._icecore_get -%}
  {%- set out = [] -%}
  {%- set on_warn = icecore._icecore_flag('samples_on_warn', false) -%}
  {%- for result in results or [] -%}
    {%- set node = get(result, 'node') -%}
    {%- set status = (get(result, 'status') | string | lower) -%}
    {%- set failures = icecore._icecore_number(get(result, 'failures')) -%}
    {%- if node is not none and (get(node, 'resource_type') | string) == 'test'
        and (status == 'fail' or (status == 'warn' and on_warn))
        and (failures is none or failures > 0)
        and (get(get(node, 'config'), 'store_failures') or get(get(node, 'config'), 'store_failures_as'))
        and get(node, 'relation_name') -%}
      {%- do out.append({'unique_id': get(node, 'unique_id'), 'relation_name': get(node, 'relation_name'), 'result': result}) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- var sample_exclude_columns as upper-case names: a list, or a comma-separated string. -#}
{% macro _icecore_sample_excluded_columns() %}
  {%- set raw = icecore._icecore_setting('sample_exclude_columns', []) -%}
  {%- set raw = (raw | string).split(',') if raw is string else (raw if raw is iterable else []) -%}
  {%- set names = [] -%}
  {%- for name in raw -%}
    {%- if (name | string | trim) -%}
      {%- do names.append((name | string | trim | upper)) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(names) }}
{% endmacro %}


{#- A column name that is safe in SQL: letters, digits and underscore only (\Z anchors: no trailing newline). -#}
{% macro _icecore_sample_valid_name(name) %}
  {{ return(modules.re.match('^[A-Za-z0-9_]+\\Z', name | string) is not none) }}
{% endmacro %}


{#- A relation name that is safe in SQL: one to three parts, each plain (letters, digits, _ and $) or double-quoted
    with "" for a quote inside. TODO: replace with icecore._sql(name, 'ident') from story 51335a30. -#}
{% macro _icecore_sample_valid_relation(relation_name) %}
  {%- set part = '(?:[A-Za-z_][A-Za-z0-9_$]*|"(?:[^"]|"")+")' -%}
  {{ return(modules.re.match('^' ~ part ~ '(?:[.]' ~ part ~ '){0,2}\\Z', relation_name | string) is not none) }}
{% endmacro %}


{#- One value: the rows as an array of objects (NULLs kept). relation_name is dbt's own quoted name of the stored failures. -#}
{% macro _icecore_sample_sql(relation_name, limit, excluded=[]) %}
  {%- if not excluded -%}
    {{ return('select array_agg(object_construct_keep_null(*)) from (select * from ' ~ relation_name ~ ' limit ' ~ (limit | int) ~ ')') }}
  {%- endif -%}
  {#- Masked in SQL, so an excluded value never leaves Snowflake: each row's keys are the relation's real column
      names, matched to the excluded names without regard to case; their value becomes '?', the name stays. -#}
  {%- set names = [] -%}
  {%- for name in excluded -%}
    {%- do names.append(icecore._sql(name | upper, 'string')) -%}
  {%- endfor -%}
  {%- set sql -%}
with s as (
  select row_number() over (order by 1) as rn, object_construct_keep_null(*) as o
  from (select * from {{ relation_name }} limit {{ limit | int }})
), m as (
  select s.rn, object_agg(f.key, iff(upper(f.key) in ({{ names | join(', ') }}), to_variant('?'), f.value)) as o
  from s, lateral flatten(input => s.o) f
  group by s.rn
)
select array_agg(o) within group (order by rn) from m
  {%- endset -%}
  {{ return(sql) }}
{% endmacro %}


{#- The sample LOG for a result and the rows read: as many rows as fit under payload_cap_kb, in order. -#}
{% macro _icecore_sample_log(result, rows) %}
  {%- set attrs = {'icecore.kind': 'sample'} -%}
  {%- do attrs.update(icecore.node_attributes(none, result)) -%}
  {%- set failures = attrs.get('dbt.test.failures') -%}
  {%- set label = (attrs.get('dbt.node.unique_id') or 'test') ~ ' failed-row sample' -%}
  {#- room for the rows: the cap less the base attributes, this record's own attributes and its message -#}
  {%- set room = icecore._icecore_payload_cap_bytes() - (tojson(icecore.base_attributes()) | length)
      - (tojson(attrs) | length) - (label | length) - 400 -%}
  {%- set kept = [] -%}
  {%- set ns = namespace(size=4) -%}
  {%- for row in rows -%}
    {#- the row as it sits in the JSON string attribute, which JSON escapes once more, plus the comma -#}
    {%- set size = (tojson(tojson(row)) | length) - 1 -%}
    {%- if ns.size + size <= room and (kept | length) == loop.index0 -%}
      {%- do kept.append(row) -%}
      {%- set ns.size = ns.size + size -%}
    {%- endif -%}
  {%- endfor -%}
  {%- do attrs.update({
      'dbt.sample.rows': tojson(kept),
      'dbt.sample.row_count': kept | length,
      'dbt.sample.truncated': (kept | length) < (rows | length),
  }) -%}
  {{ return({
      'type': 'log',
      'level': 'info',
      'message': label ~ ': ' ~ (kept | length) ~ (' of ' ~ failures if failures is not none else '') ~ ' failed rows',
      'attrs': attrs,
  }) }}
{% endmacro %}
