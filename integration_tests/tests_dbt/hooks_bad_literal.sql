{#- UC1 write-path fixture (tests/test_write_path.py): a test that errors with a data literal in Snowflake's
    message ("Numeric value 'secret_value' is not recognized"), to see icecore redact dbt.result.message. It
    needs no object and is tagged icecore_hooks, so the default selector leaves it out. -#}
{{ config(tags=['icecore_hooks']) }}
select 1 as x where cast('secret_value' as number) > 0
