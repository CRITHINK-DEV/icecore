select 10 as order_id, 1 as customer_id, 12.5 as amount, current_timestamp() as ordered_at
union all select 11, 2, 7.0, current_timestamp()
union all select 12, null, 3.0, current_timestamp()
