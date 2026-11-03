{#- Skipped whenever fct_payments fails. -#}
select count(*) as payments, sum(paid_amount) as paid_total from {{ ref('fct_payments') }}
