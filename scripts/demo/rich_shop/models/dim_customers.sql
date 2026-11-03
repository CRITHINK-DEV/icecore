{#- From demo_stage 11 lifetime_amount is a float instead of a fixed-point number (a type change). -#}
select
    c.customer_id,
    c.first_name,
    c.country,
    count(o.order_id) as orders,
    {%- if var('demo_stage', 0) | int >= 11 %}
    coalesce(sum(o.amount), 0)::float as lifetime_amount
    {%- else %}
    coalesce(sum(o.amount), 0) as lifetime_amount
    {%- endif %}
from {{ ref('stg_customers') }} c
left join {{ ref('stg_orders') }} o on o.customer_id = c.customer_id
group by 1, 2, 3
