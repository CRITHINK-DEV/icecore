{#-
  UC1 attribute namespace (spec section 4): the keys every icecore record carries, built in one place.

  base_attributes()            invocation-level keys, on every record (GATE1.md: each CALL is its own trace,
                               so `dbt.invocation_id` is how a run is grouped).
  node_attributes(node, result) node keys (`dbt.node.*`, `dbt.test.*` for tests) and, given a run result,
                               `dbt.result.*` and `snow.query.id`.

  Values stay typed: numbers are numbers, booleans are booleans. A value that is missing is left out rather than
  written as null. Nodes and results may be graph dicts or dbt objects (`results` in on-run-end).
-#}

{% macro base_attributes() %}
  {{ return({
      'dbt.invocation_id': invocation_id,
      'dbt.runtime': icecore._icecore_runtime(),
      'dbt.command': flags.WHICH,
      'dbt.target': target.name,
      'dbt.project': project_name,
      'icecore.version': icecore._icecore_package_version(),
      'icecore.schema_version': icecore._icecore_schema_version(),
  }) }}
{% endmacro %}


{% macro node_attributes(node, result=none) %}
  {%- set get = icecore._icecore_get -%}
  {%- if node is none and result is not none -%}
    {%- set node = get(result, 'node') -%}
  {%- endif -%}
  {%- set out = {} -%}
  {%- if node is not none -%}
    {%- set config = get(node, 'config') -%}
    {%- set resource_type = get(node, 'resource_type') | string -%}
    {%- do icecore._icecore_put(out, 'dbt.node.unique_id', get(node, 'unique_id')) -%}
    {%- do icecore._icecore_put(out, 'dbt.node.type', resource_type) -%}
    {%- do icecore._icecore_put(out, 'dbt.node.name', get(node, 'name')) -%}
    {%- do icecore._icecore_put(out, 'dbt.node.materialization', get(config, 'materialized')) -%}
    {%- do icecore._icecore_put(out, 'dbt.node.relation', icecore._icecore_node_relation(node)) -%}
    {%- if resource_type == 'test' -%}
      {%- set test_metadata = get(node, 'test_metadata') -%}
      {%- do icecore._icecore_put(out, 'dbt.test.name', get(test_metadata, 'name') if test_metadata is not none else get(node, 'name')) -%}
      {%- do icecore._icecore_put(out, 'dbt.test.type', 'generic' if test_metadata is not none else 'singular') -%}
      {%- do icecore._icecore_put(out, 'dbt.test.column', get(node, 'column_name')) -%}
      {%- do icecore._icecore_put(out, 'dbt.test.severity', get(config, 'severity')) -%}
    {%- endif -%}
  {%- endif -%}
  {%- if result is not none -%}
    {%- set response = get(result, 'adapter_response') -%}
    {%- set status = get(result, 'status') -%}
    {%- do icecore._icecore_put(out, 'dbt.result.status', none if status is none else status | string) -%}
    {%- do icecore._icecore_put(out, 'dbt.result.rows_affected', icecore._icecore_number(get(response, 'rows_affected'))) -%}
    {%- do icecore._icecore_put(out, 'dbt.result.execution_time_s', icecore._icecore_number(get(result, 'execution_time'))) -%}
    {%- do icecore._icecore_put(out, 'dbt.result.message', icecore._icecore_result_message(get(result, 'message'))) -%}
    {%- do icecore._icecore_put(out, 'dbt.test.failures', icecore._icecore_number(get(result, 'failures'))) -%}
    {%- do icecore._icecore_put(out, 'snow.query.id', get(response, 'query_id')) -%}
  {%- endif -%}
  {{ return(out) }}
{% endmacro %}


{#- dbt.result.message as var('icecore').result_message wants it. dbt's message can quote data values from a
    Snowflake error (Numeric value 'jane@x.com' is not recognized), and the event table may be readable by more
    people than the data, so the default is `redacted`: every single-quoted literal becomes '?' and every
    double-quoted one "?" (doubled and backslash-escaped quotes inside a literal are part of it; an
    unterminated literal is redacted to the end), then the text is cut at 1,000 characters. `full` keeps the
    message as dbt wrote it, `none` leaves the key out. An unknown mode reads as `redacted`. -#}
{% macro _icecore_result_message(message) %}
  {%- if message is none -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set mode = icecore._icecore_setting('result_message', 'redacted') | string | trim | lower -%}
  {%- if mode == 'none' -%}
    {{ return(none) }}
  {%- elif mode == 'full' -%}
    {{ return(message | string) }}
  {%- endif -%}
  {{ return(icecore._icecore_redact_literals(message | string)[:1000]) }}
{% endmacro %}


{% macro _icecore_redact_literals(text) %}
  {%- set q, dq, bs = "'", '"', '\\\\' -%}
  {%- set pattern = '(?s)(' ~ q ~ ')(?:[^' ~ q ~ bs ~ ']|' ~ bs ~ '.|' ~ q ~ q ~ ')*(?:' ~ q ~ '|$)'
      ~ '|(' ~ dq ~ ')(?:[^' ~ dq ~ bs ~ ']|' ~ bs ~ '.|' ~ dq ~ dq ~ ')*(?:' ~ dq ~ '|$)' -%}
  {{ return(modules.re.sub(pattern, '\\1\\2?\\1\\2', text)) }}
{% endmacro %}


{#- Record payload layout version; views use it to read older records. Bump when keys change meaning. -#}
{% macro _icecore_schema_version() %}
  {{ return(1) }}
{% endmacro %}


{#- The installed icecore version, from the package's own dbt_project.yml as dbt loaded it
    (the runtime config's dependency map). 'unknown' when the runtime does not expose it. -#}
{% macro _icecore_package_version() %}
  {%- set ref_fn = builtins.get('ref') -%}
  {%- set config = ref_fn.config if ref_fn is not none and ref_fn.config is defined else none -%}
  {%- set dependencies = config.dependencies if config is not none and config.dependencies is defined else {} -%}
  {%- set package = dependencies.get('icecore') -%}
  {{ return('unknown' if package is none else package.version | string) }}
{% endmacro %}


{#- Where dbt runs: fusion (dbt 2.x), cloud (a DBT_CLOUD_* variable is set) or core.
    snowflake_native (EXECUTE DBT PROJECT) is not detected yet: no spike measured what that runtime exposes
    (GATE1.md, open item 5). -#}
{% macro _icecore_runtime() %}
  {%- if (dbt_version | string).split('.')[0] | int >= 2 -%}
    {{ return('fusion') }}
  {%- endif -%}
  {%- for name in ['DBT_CLOUD_RUN_ID', 'DBT_CLOUD_PROJECT_ID', 'DBT_CLOUD_JOB_ID', 'DBT_CLOUD_ENVIRONMENT_ID'] -%}
    {%- if env_var(name, '') -%}
      {{ return('cloud') }}
    {%- endif -%}
  {%- endfor -%}
  {{ return('core') }}
{% endmacro %}


{#- Quoted "DB"."SCHEMA"."NAME" of a relational node (model, seed, snapshot, source), none for others.
    Quoting follows the node (source `quoting:`, model `config.quoting`), defaulting to Snowflake's unquoted. -#}
{% macro _icecore_node_relation(node) %}
  {%- set get = icecore._icecore_get -%}
  {%- set resource_type = get(node, 'resource_type') | string -%}
  {%- if resource_type not in ['model', 'seed', 'snapshot', 'source'] -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set identifier = get(node, 'identifier') if resource_type == 'source' else (get(node, 'alias') or get(node, 'name')) -%}
  {%- set database, schema = get(node, 'database'), get(node, 'schema') -%}
  {%- if not database or not schema or not identifier -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set quoting = get(node, 'quoting') if resource_type == 'source' else get(get(node, 'config'), 'quoting') -%}
  {%- set relation = api.Relation.create(database=database, schema=schema, identifier=identifier, quote_policy={
      'database': get(quoting, 'database') or false,
      'schema': get(quoting, 'schema') or false,
      'identifier': get(quoting, 'identifier') or false,
  }) -%}
  {{ return(icecore._icecore_quoted_name(relation)) }}
{% endmacro %}


{#- obj[key] for a dict or an object, none when obj is none or the key is missing. -#}
{% macro _icecore_get(obj, key) %}
  {%- if obj is none or obj is not defined -%}
    {{ return(none) }}
  {%- endif -%}
  {%- set value = obj[key] -%}
  {{ return(value if value is defined else none) }}
{% endmacro %}


{#- Set key only when value is not none (missing values are left out, not written as null). -#}
{% macro _icecore_put(out, key, value) %}
  {%- if value is not none -%}
    {%- do out.update({key: value}) -%}
  {%- endif -%}
{% endmacro %}


{#- A number as a number: numbers and booleans pass through, numeric strings are parsed, anything else is none. -#}
{% macro _icecore_number(value) %}
  {%- if value is none or value is boolean or value is number -%}
    {{ return(value) }}
  {%- endif -%}
  {%- set text = value | string | trim -%}
  {%- if modules.re.match('^[-+]?[0-9]+$', text) -%}
    {{ return(text | int) }}
  {%- elif modules.re.match('^[-+]?([0-9]+[.][0-9]*|[.][0-9]+)([eE][-+]?[0-9]+)?$|^[-+]?[0-9]+[eE][-+]?[0-9]+$', text) -%}
    {{ return(text | float) }}
  {%- endif -%}
  {{ return(none) }}
{% endmacro %}
