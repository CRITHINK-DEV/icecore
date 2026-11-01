{#- UC1 hook fixtures (tests/test_hooks.py, test_timings.py, test_node_results.py): three views in a chain,
    hooks_a -> hooks_b -> hooks_c, named with the run_id and dropped by the tests. -#}
{{ config(materialized='view', alias='hooks_a_' ~ var('run_id', 'none')) }}
select 1 as id union all select 2 union all select 3 union all select 4
