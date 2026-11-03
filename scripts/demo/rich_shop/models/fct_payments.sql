{#- demo_break_payments makes this model fail at run time (division by zero): the demo's failed model. -#}
select
    order_id,
    amount / {{ 0 if var('demo_break_payments', false) else 1 }} as paid_amount
from {{ ref('stg_orders') }}
