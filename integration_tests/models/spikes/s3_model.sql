{#- Spike S3: the one model the hook-coverage commands run. A view named with the run_id, in SPIKES. -#}
{{ config(materialized='view', alias='s3_model_' ~ var('run_id', 'none'), tags=['s3_spike']) }}
select 1 as id, current_timestamp() as updated_at
