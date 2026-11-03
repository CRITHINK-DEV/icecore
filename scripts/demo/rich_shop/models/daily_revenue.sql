{#- Built in the first runs only: its meta.sla then goes unmet and its freshness test breaches. -#}
select to_date(ordered_at) as day, sum(amount) as revenue from {{ ref('stg_orders') }} group by 1
