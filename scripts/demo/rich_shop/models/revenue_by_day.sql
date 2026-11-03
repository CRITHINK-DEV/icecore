{#- One row per day with orders: its row count follows the spread of the seed. Skipped when a fct_orders test fails. -#}
select to_date(ordered_at) as day, count(*) as orders, sum(amount) as revenue from {{ ref('fct_orders') }} group by 1
