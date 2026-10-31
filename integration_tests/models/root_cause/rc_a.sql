{{ config(materialized='ephemeral') }}
select id from {{ source('rc_raw', 'orders') }}
