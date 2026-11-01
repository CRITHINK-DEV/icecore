{{ config(materialized='view', alias='rce_c_' ~ var('run_id', 'none'), tags=['root_cause_e2e']) }}
select null::number as id from {{ ref('rce_b') }}
