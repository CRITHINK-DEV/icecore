{#- With --vars '{shop_fail: true}' two orders lose their customer_id, so the not_null test below fails. -#}
select
    order_id,
    case when {{ 'true' if var('shop_fail', false) else 'false' }} and order_id in (3, 5) then null else customer_id end as customer_id,
    status,
    amount,
    ordered_at
from {{ ref('stg_orders') }}
