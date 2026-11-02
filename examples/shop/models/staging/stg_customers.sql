select
    id as customer_id,
    first_name,
    country
from {{ source('raw', 'raw_customers') }}
