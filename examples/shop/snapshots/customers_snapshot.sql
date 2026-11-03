{% snapshot customers_snapshot %}
{{ config(target_schema=target.schema, unique_key='customer_id', strategy='check', check_cols=['first_name', 'country']) }}
select customer_id, first_name, country from {{ ref('stg_customers') }}
{% endsnapshot %}
