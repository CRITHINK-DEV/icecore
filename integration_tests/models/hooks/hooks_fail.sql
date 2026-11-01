{#- Fails only with --vars '{hooks_fail: true}': the view then selects a column that does not exist. It depends
    on nothing, so selecting hooks_a+ never includes it and its failure skips no other node. -#}
{{ config(materialized='view', alias='hooks_fail_' ~ var('run_id', 'none')) }}
select {{ 'no_such_column' if var('hooks_fail', false) else '1' }} as id
