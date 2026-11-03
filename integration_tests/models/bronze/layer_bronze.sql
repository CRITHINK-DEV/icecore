{{ config(materialized='table', alias='layer_bronze_' ~ var('run_id', 'none')) }}
select 1 as id union all select 2
