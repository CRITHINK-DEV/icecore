{{ config(materialized='view', alias='hooks_c_' ~ var('run_id', 'none')) }}
select id from {{ ref('hooks_b') }}
