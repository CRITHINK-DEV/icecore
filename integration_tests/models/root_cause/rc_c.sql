{{ config(materialized='ephemeral') }}
select id from {{ ref('rc_b') }}
