{#- UC1 test fixture: logs icecore.base_attributes() while a real `dbt build` runs (tests/test_attributes.py).
    Enabled only with --vars '{it_attributes_build: true}', so ordinary builds never see it. It selects no row:
    the test passes and creates nothing. -#}
{{ config(enabled=var('it_attributes_build', false)) }}
{% if execute %}
  {% do log('icecore_it_result:' ~ tojson(icecore.base_attributes()), info=True) %}
{% endif %}
select 1 as x where false
