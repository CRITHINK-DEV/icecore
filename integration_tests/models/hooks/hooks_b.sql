{{ config(materialized='view', alias='hooks_b_' ~ var('run_id', 'none')) }}
select id from {{ ref('hooks_a') }}
