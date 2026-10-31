{{ config(materialized='ephemeral') }}
select id from {{ ref('rc_b') }}
union all
select id from {{ ref('rc_e') }}
union all
select id from {{ ref('rc_snap') }}
