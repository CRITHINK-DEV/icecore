{#- UC5 setup step `create_tasks`: ONE list of the package's tasks (_icecore_task_entries), run in order by create_tasks.
    A story that adds a task adds an entry here; setup calls only create_tasks. Each entry is
    {name, create}: `create` names a macro that takes (schema, capabilities) and returns {task, status, resumed, printed},
    status ok, skipped or the guarded error. Tasks are created suspended unless their own var resumes them. -#}

{% macro create_tasks(schema=none, capabilities=none) %}
  {%- set out = {'tasks': [], 'printed': []} -%}
  {%- if not execute -%}{{ return(out) }}{%- endif -%}
  {%- for entry in icecore._icecore_task_entries() -%}
    {%- set result = icecore[entry['create']](schema=schema, capabilities=capabilities) -%}
    {%- do out['tasks'].append({'name': entry['name'], 'task': result['task'], 'status': result['status'], 'resumed': result['resumed']}) -%}
    {%- do out['printed'].extend(result['printed']) -%}
  {%- endfor -%}
  {{ return(out) }}
{% endmacro %}


{#- The task list. The silence task (UC6) joins as {'name': 'silence', 'create': 'create_silence_task'}. -#}
{% macro _icecore_task_entries() %}
  {{ return([
      {'name': 'cortex_refresh', 'create': 'create_refresh_cortex_task'},
  ]) }}
{% endmacro %}
