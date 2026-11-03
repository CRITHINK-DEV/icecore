{{ config(materialized='view', alias='layer_brz_' ~ var('run_id', 'none')) }}
select 1 as id
