{#- Parse-only fixture for the walk_upstream tests; excluded from default selection. -#}
{% snapshot rc_snap %}
{{ config(target_schema=target.schema, unique_key='id', strategy='check', check_cols='all', tags=['root_cause_fixture']) }}
select id from {{ ref('rc_seed') }}
{% endsnapshot %}
