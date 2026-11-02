{{ config(materialized='view', alias='rce_b_' ~ var('run_id', 'none'), tags=['root_cause_e2e']) }}
select id from {{ ref('rce_a') }}
