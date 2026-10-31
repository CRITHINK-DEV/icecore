{#- Test-only bridge: call a macro of the icecore package by name and log its return value as
    `icecore_it_result:<json>`, so pytest can read it back from the dbt event stream (helpers/macros.py). -#}
{% macro it_call(name, kwargs={}) %}
  {% set result = icecore[name](**kwargs) %}
  {{ log("icecore_it_result:" ~ tojson(result), info=True) }}
{% endmacro %}
