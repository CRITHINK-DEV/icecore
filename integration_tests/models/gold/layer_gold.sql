{{ config(materialized='view', alias='layer_gold_' ~ var('run_id', 'none')) }}
select id from {{ ref('layer_bronze') }}
