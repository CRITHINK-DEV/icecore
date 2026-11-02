{#- S08 regression fixture (tests/test_regression.py): two identical rows whose values are what once broke the
    write path: a string with a quote, `$$` and a backslash, a decimal, and a nested object. Each column has a
    unique test that fails, so the sample carries the values. -#}
{{ config(materialized='view', alias='hooks_weird_' ~ var('run_id', 'none')) }}
select 'it''s a $$ \\ "q" end' as k, 1.50::number(3, 2) as d, parse_json('{"a": [1, {"b": "x $$ \\\\ y"}]}') as v
union all
select 'it''s a $$ \\ "q" end', 1.50::number(3, 2), parse_json('{"a": [1, {"b": "x $$ \\\\ y"}]}')
