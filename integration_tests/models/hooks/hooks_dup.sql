{#- UC1 sample fixture: 20 rows, every id, code and big value twice (10 distinct values each). `big` is 20,000
    characters wide, so ten failed rows of a unique test on it are about 200 KB. -#}
{{ config(materialized='view', alias='hooks_dup_' ~ var('run_id', 'none')) }}
with n as (select row_number() over (order by seq4()) as i from table(generator(rowcount => 20)))
select mod(i, 10) + 1 as id, 'c' || (mod(i, 10) + 1) as code, repeat(chr(64 + mod(i, 10) + 1), 20000) as big
from n
