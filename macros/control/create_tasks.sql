{#- UC5 setup step `create_tasks`: ONE list of the package's tasks (_icecore_task_entries), run in order by create_tasks.
    A story that adds a task adds an entry here; setup calls only create_tasks. Each entry is
    {name, create}: `create` names a macro that takes (schema, capabilities) and returns {task, status, resumed, printed},
    status ok, skipped or the guarded error. Tasks are created suspended unless their own var resumes them.
    The silence task is created when var alerts is true or silence_task_resume is true, and resumed unless
    silence_task_resume is false; an existing one that is not asked for is reported with its SUSPEND and DROP
    statements. The Cortex task does not depend on `alerts` (it has its own conditions). -#}

{% macro create_tasks(schema=none, capabilities=none) %}
  {%- set out = {'tasks': [], 'printed': []} -%}
  {%- if not execute -%}{{ return(out) }}{%- endif -%}
  {%- do icecore.create_sla_declared_view(schema) -%}
  {%- for entry in icecore._icecore_task_entries() -%}
    {#- explicit dispatch (no macro lookup by name, Fusion-safe) -#}
    {%- if entry['name'] == 'cortex_refresh' -%}
      {%- set result = icecore.create_refresh_cortex_task(schema=schema, capabilities=capabilities) -%}
    {%- elif entry['name'] == 'silence' -%}
      {%- if icecore._icecore_control_enabled('silence_task_resume') -%}
        {%- set result = icecore.create_silence_task(schema=schema) -%}
      {%- else -%}
        {#- not asked for: no silence task, and an existing one is reported, never touched -#}
        {%- do icecore._icecore_warn_unmanaged(schema, 'TASK') -%}
        {%- set result = {'task': none, 'status': 'skipped', 'resumed': false, 'printed': []} -%}
      {%- endif -%}
    {%- else -%}
      {{ exceptions.raise_compiler_error('icecore: unknown task entry ' ~ entry['name']) }}
    {%- endif -%}
    {%- do out['tasks'].append({'name': entry['name'], 'task': result['task'], 'status': result['status'], 'resumed': result['resumed']}) -%}
    {%- do out['printed'].extend(result['printed']) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The task list: the silence task (UC6) and the Cortex model refresh task (UC3). -#}
{% macro _icecore_task_entries() %}
  {{ return([
      {'name': 'silence', 'create': 'create_silence_task'},
      {'name': 'cortex_refresh', 'create': 'create_refresh_cortex_task'},
  ]) }}
{% endmacro %}
