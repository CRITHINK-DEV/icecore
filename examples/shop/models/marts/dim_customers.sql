select
    c.customer_id,
    c.first_name,
    c.country,
    count(o.order_id) as orders,
    coalesce(sum(o.amount), 0) as lifetime_amount
from {{ ref('stg_customers') }} c
left join {{ ref('stg_orders') }} o on o.customer_id = c.customer_id
group by 1, 2, 3
