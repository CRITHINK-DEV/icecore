{#- With --vars '{shop_fail: true}' two orders lose their customer_id, so the not_null test fails. From demo_stage 7 a
    column `channel` is added (a schema change). -#}
select
    order_id,
    case when {{ 'true' if var('shop_fail', false) else 'false' }} and order_id in (3, 5) then null else customer_id end as customer_id,
    status,
    amount,
    ordered_at
    {%- if var('demo_stage', 0) | int >= 7 %},
    'web' as channel
    {%- endif %}
from {{ ref('stg_orders') }}
