{#- Spike S3: the one snapshot the hook-coverage commands run. Named with the run_id, in SPIKES. -#}
{% snapshot s3_snapshot %}
{{ config(target_schema=target.schema, alias='s3_snapshot_' ~ var('run_id', 'none'), unique_key='id',
          strategy='check', check_cols='all', tags=['s3_spike']) }}
select 1 as id, 'a' as v
{% endsnapshot %}
