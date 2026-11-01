{#- Root-cause end-to-end fixtures (tests/test_root_cause_e2e.py): rce_a -> rce_b -> rce_c, a not_null test on rce_c.
    Named with the run_id and dropped by the tests. rce_a gains a column with --vars '{rce_extra_col: true}', which is
    the upstream schema change the test turns into a signal. rce_c has no id, so its test fails. -#}
{{ config(materialized='view', alias='rce_a_' ~ var('run_id', 'none'), tags=['root_cause_e2e']) }}
select 1 as id{{ ', 1 as extra' if var('rce_extra_col', false) else '' }}
