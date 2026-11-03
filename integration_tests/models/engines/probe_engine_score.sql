{#- Parse probe: a model that calls icecore.score must parse (execute is false at parse time).
    Disabled so no run ever builds it; dbt still renders it while parsing. -#}
{{ config(enabled=false) }}
{% set result = icecore.score(
    series_sql="select '2026-10-01'::timestamp_ntz as observed_at, 1::float as value",
    current_value=1, engine='auto') %}
select {{ result.score if result.score is not none else 'null' }} as score
