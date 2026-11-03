{#- UC8 compat views `dbt_invocations` and `dbt_run_results`, with the columns, order and types of Elementary's tables of
    those names (Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION: get_dbt_invocations_empty_table_query
    and get_dbt_run_results_empty_table_query). They select from the core views INVOCATIONS and NODE_EXECUTIONS of the
    obs schema and nothing else (never base_events), so each has exactly its core view's rows: row counts match.

    Timestamps Elementary stores as TEXT (run_started_at, run_completed_at, generated_at, execute_*), icecore writes as
    text Snowflake can CAST: `YYYY-MM-DD HH24:MI:SS` (Elementary's own format) for the invocation's times, with
    microseconds for the execute times. created_at is TIMESTAMP_NTZ.

    dbt_invocations, from INVOCATIONS:
      invocation_id, command, dbt_version, target_name (target)
      project_name       the dbt project of the run (dbt.project): a real value, edr shows it in the report
      elementary_version the pinned Elementary release (_icecore_elementary_version): edr checks its own version against
                         it, so the compat views claim the release they were built to match
      run_started_at, run_completed_at   started_at and ended_at (dbt's clock, the dbt host's)
      generated_at, created_at           ended_at, else started_at (a run still going has no end)
    Typed NULLs: job_id, job_name, job_run_id, full_refresh, invocation_vars, vars, target_database, target_schema,
    target_profile_name, threads, selected, yaml_selector, project_id, env, env_id, cause_category, cause,
    pull_request_id, git_sha, orchestrator, dbt_user, job_url, job_run_url, account_id, target_adapter_specific_fields.

    dbt_run_results, from NODE_EXECUTIONS (one row per node per invocation; Elementary also loads every result of the run):
      model_execution_id invocation_id || '.' || unique_id, as Elementary builds it
      unique_id, invocation_id, name, status, rows_affected, query_id, materialization, execution_time
      message            dbt.result.message, capped with its record (payload_cap_kb, default 64 KB)
      resource_type      type
      execute_started_at, execute_completed_at   started_at and completed_at (dbt's clock)
      generated_at, created_at                   recorded_at: Snowflake's clock, when the result record landed
    Typed NULLs: compile_started_at, compile_completed_at, full_refresh, compiled_code, failures, thread_id,
    adapter_response, group_name.
    The two clocks are never subtracted from each other. -#}


{#- The group of views appended to the compat list: [{name, sql}]. -#}
{% macro _icecore_elementary_run_views(obs) %}
  {{ return([
    {'name': 'DBT_INVOCATIONS', 'sql': icecore.dbt_invocations_view_sql(obs)},
    {'name': 'DBT_RUN_RESULTS', 'sql': icecore.dbt_run_results_view_sql(obs)},
  ]) }}
{% endmacro %}


{#- The Elementary dbt package release the compat views target: the value of elementary_dbt_package in
    macros/compat/ELEMENTARY_VERSION (a test keeps the two in step). -#}
{% macro _icecore_elementary_version() %}
  {{ return('0.26.1') }}
{% endmacro %}


{% macro dbt_invocations_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_dbt_invocations_columns(), obs ~ '."INVOCATIONS"')) }}
{% endmacro %}


{% macro dbt_run_results_view_sql(obs=none) %}
  {%- set obs = obs or icecore.obs_schema() -%}
  {{ return(icecore._icecore_compat_select(icecore._icecore_dbt_run_results_columns(), obs ~ '."NODE_EXECUTIONS"')) }}
{% endmacro %}


{#- Text of a timestamp in Elementary's format, over the alias t. -#}
{% macro _icecore_elementary_text(expression, micros=false) %}
  {{ return("to_varchar(" ~ expression ~ ", '" ~ ('YYYY-MM-DD HH24:MI:SS.FF6' if micros else 'YYYY-MM-DD HH24:MI:SS') ~ "')") }}
{% endmacro %}


{#- [{name, type, expr}] in Elementary's order. expr is over the alias t (INVOCATIONS), none = typed NULL. -#}
{% macro _icecore_dbt_invocations_columns() %}
  {%- set text = icecore._icecore_elementary_text -%}
  {%- set last = 'coalesce(t.ended_at, t.started_at)' -%}
  {{ return([
    {'name': 'invocation_id', 'type': 'long_string', 'expr': 't.invocation_id'},
    {'name': 'job_id', 'type': 'long_string', 'expr': none},
    {'name': 'job_name', 'type': 'long_string', 'expr': none},
    {'name': 'job_run_id', 'type': 'long_string', 'expr': none},
    {'name': 'run_started_at', 'type': 'string', 'expr': text('t.started_at')},
    {'name': 'run_completed_at', 'type': 'string', 'expr': text('t.ended_at')},
    {'name': 'generated_at', 'type': 'string', 'expr': text(last)},
    {'name': 'created_at', 'type': 'timestamp', 'expr': last},
    {'name': 'command', 'type': 'string', 'expr': 't.command'},
    {'name': 'dbt_version', 'type': 'string', 'expr': 't.dbt_version'},
    {'name': 'elementary_version', 'type': 'string', 'expr': icecore._sql(icecore._icecore_elementary_version(), 'string')},
    {'name': 'full_refresh', 'type': 'boolean', 'expr': none},
    {'name': 'invocation_vars', 'type': 'long_string', 'expr': none},
    {'name': 'vars', 'type': 'long_string', 'expr': none},
    {'name': 'target_name', 'type': 'string', 'expr': 't.target'},
    {'name': 'target_database', 'type': 'string', 'expr': none},
    {'name': 'target_schema', 'type': 'string', 'expr': none},
    {'name': 'target_profile_name', 'type': 'string', 'expr': none},
    {'name': 'threads', 'type': 'int', 'expr': none},
    {'name': 'selected', 'type': 'long_string', 'expr': none},
    {'name': 'yaml_selector', 'type': 'long_string', 'expr': none},
    {'name': 'project_id', 'type': 'string', 'expr': none},
    {'name': 'project_name', 'type': 'string', 'expr': 't.project'},
    {'name': 'env', 'type': 'string', 'expr': none},
    {'name': 'env_id', 'type': 'string', 'expr': none},
    {'name': 'cause_category', 'type': 'string', 'expr': none},
    {'name': 'cause', 'type': 'long_string', 'expr': none},
    {'name': 'pull_request_id', 'type': 'string', 'expr': none},
    {'name': 'git_sha', 'type': 'string', 'expr': none},
    {'name': 'orchestrator', 'type': 'string', 'expr': none},
    {'name': 'dbt_user', 'type': 'string', 'expr': none},
    {'name': 'job_url', 'type': 'string', 'expr': none},
    {'name': 'job_run_url', 'type': 'string', 'expr': none},
    {'name': 'account_id', 'type': 'string', 'expr': none},
    {'name': 'target_adapter_specific_fields', 'type': 'long_string', 'expr': none},
  ]) }}
{% endmacro %}


{#- [{name, type, expr}] in Elementary's order. expr is over the alias t (NODE_EXECUTIONS), none = typed NULL. -#}
{% macro _icecore_dbt_run_results_columns() %}
  {%- set text = icecore._icecore_elementary_text -%}
  {{ return([
    {'name': 'model_execution_id', 'type': 'long_string', 'expr': "t.invocation_id || '.' || t.unique_id"},
    {'name': 'unique_id', 'type': 'long_string', 'expr': 't.unique_id'},
    {'name': 'invocation_id', 'type': 'string', 'expr': 't.invocation_id'},
    {'name': 'generated_at', 'type': 'string', 'expr': text('t.recorded_at')},
    {'name': 'created_at', 'type': 'timestamp', 'expr': 't.recorded_at'},
    {'name': 'name', 'type': 'long_string', 'expr': 't.name'},
    {'name': 'message', 'type': 'long_string', 'expr': 't.message'},
    {'name': 'status', 'type': 'string', 'expr': 't.status'},
    {'name': 'resource_type', 'type': 'string', 'expr': 't.type'},
    {'name': 'execution_time', 'type': 'float', 'expr': 't.execution_time_s'},
    {'name': 'execute_started_at', 'type': 'string', 'expr': text('t.started_at', true)},
    {'name': 'execute_completed_at', 'type': 'string', 'expr': text('t.completed_at', true)},
    {'name': 'compile_started_at', 'type': 'string', 'expr': none},
    {'name': 'compile_completed_at', 'type': 'string', 'expr': none},
    {'name': 'rows_affected', 'type': 'bigint', 'expr': 't.rows_affected'},
    {'name': 'full_refresh', 'type': 'boolean', 'expr': none},
    {'name': 'compiled_code', 'type': 'long_string', 'expr': none},
    {'name': 'failures', 'type': 'bigint', 'expr': none},
    {'name': 'query_id', 'type': 'string', 'expr': 't.query_id'},
    {'name': 'thread_id', 'type': 'string', 'expr': none},
    {'name': 'materialization', 'type': 'string', 'expr': 't.materialization'},
    {'name': 'adapter_response', 'type': 'string', 'expr': none},
    {'name': 'group_name', 'type': 'string', 'expr': none},
  ]) }}
{% endmacro %}
