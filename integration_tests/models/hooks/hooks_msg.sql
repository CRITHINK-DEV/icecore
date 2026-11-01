{#- Fails only with --vars '{hooks_msg: true}': Snowflake's error then quotes the value (a table, so the cast runs at build) 'it's a \ test' (a quote and a
    backslash), which tests/test_concurrency_escaping.py reads back from the event table. -#}
{{ config(materialized='table', alias='hooks_msg_' ~ var('run_id', 'none')) }}
select {{ "'it''s a \\\\ test'::int" if var('hooks_msg', false) else '1' }} as id
