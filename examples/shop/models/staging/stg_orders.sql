select
    id as order_id,
    customer_id,
    status,
    amount,
    ordered_at
from {{ source('raw', 'raw_orders') }}
