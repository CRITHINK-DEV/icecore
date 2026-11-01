# Views reference

`icecore.setup` creates these views in `<target_db>.<obs_schema>` (default `ICECORE_OBS`). They are the public interface: read them, not the event table. Each one starts from the same filter over the event table, `icecore.base_events()`, so records of other tools and other environments are never returned. The filter is on the [conventions page](../conventions.md#the-base-filter).

Column types below are from `DESCRIBE VIEW` on the sandbox. Text columns are `VARCHAR`, times are `TIMESTAMP_NTZ(9)`. A key that a record lacks reads as null and never fails a view.

Every time column is the event's `TIMESTAMP`, because `OBSERVED_TIMESTAMP` is NULL on span and span-event records (spike S3). Records reach the table with a p95 lag of 7.73 s and a worst record at 20.3 s (S3.md). A view read right after a run can miss its last records.

`COLUMN` is a reserved word in Snowflake. Quote it: `"COLUMN"`.

Re-running `icecore.setup` replaces the views with `COPY GRANTS`, so grants you made on them stay.

Views not merged yet are **coming**: `INVOCATIONS`, `NODE_EXECUTIONS`, `SILENCE_EVENTS` and `ALERTS_FEED`.

## Who can read

| Reader | Needs |
| --- | --- |
| Anyone who queries a view | USAGE on the database and the schema, SELECT on the view |
| The same, when `setup` resolved the account event table `SNOWFLAKE.TELEMETRY.EVENTS` | Also the `EVENTS_VIEWER` application role. The views then read `SNOWFLAKE.TELEMETRY.EVENTS_VIEW` |
| The role that runs dbt | USAGE on the database, the schema and both procedures. Writing needs no `EVENTS_VIEWER` |

```sql
GRANT USAGE ON DATABASE <db> TO ROLE <reader>;
GRANT USAGE ON SCHEMA <db>.ICECORE_OBS TO ROLE <reader>;
GRANT SELECT ON ALL VIEWS IN SCHEMA <db>.ICECORE_OBS TO ROLE <reader>;
GRANT APPLICATION ROLE SNOWFLAKE.EVENTS_VIEWER TO ROLE <reader>;  -- only for the account table
```

Source: `spikes/GATE1.md`, "What setup creates and the grants it needs".

## `TEST_RESULTS`

One row per test per invocation. Reads the `node_result` log records of test nodes (unique id starting `test.`). A retried or duplicated record collapses to the latest one per invocation and test.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `project` | text | Root dbt project | `dbt.project` |
| `test_unique_id` | text | The test's dbt unique id | `dbt.node.unique_id` |
| `test_name` | text | Test name, such as `not_null` | `dbt.test.name` |
| `test_type` | text | `generic` or `singular` | `dbt.test.type` |
| `COLUMN` | text | Tested column | `dbt.test.column` |
| `severity` | text | `error` or `warn` | `dbt.test.severity` |
| `status` | text | `pass`, `fail`, `warn`, `error` | `dbt.result.status` |
| `failures` | number | Failing rows | `dbt.test.failures` |
| `message` | text | dbt's result message, redacted by default ([`result_message`](../configuration.md#result_message)) | `dbt.result.message` |
| `execution_time_s` | float | Seconds | `dbt.result.execution_time_s` |
| `engine` | text | Anomaly engine, when a scored test wrote it | `dbt.anomaly.engine` |
| `expected`, `lower`, `upper`, `score` | float | The anomaly band and score. Null unless a scored test wrote them | `dbt.anomaly.expected`, `.lower`, `.upper`, `.score` |
| `executed_at` | time | When the record was written | event `TIMESTAMP` |
| `query_id` | text | The test's Snowflake query | `snow.query.id` |

There is no `model_unique_id` column: no emitter writes the tested model's id on the node result.

## `METRICS`

One row per metric a monitor computed, from the `icecore.metric` span events that `emit_metric` writes. Today the `icecore.freshness` and `icecore.volume` tests write them: `freshness_lag_seconds` and `row_count`. The `icecore.anomaly` warn events repeat their metric event's keys, so they are not rows here.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `relation` | text | The quoted `"DB"."SCHEMA"."NAME"` | `dbt.node.relation` |
| `COLUMN` | text | Column, for column metrics | `dbt.metric.column` |
| `dimension` | text | Dimension value, for dimension metrics | `dbt.metric.dimension` |
| `metric` | text | Metric name | `dbt.metric.name` |
| `observed_at` | time | When the record was written | event `TIMESTAMP` |
| `value` | float | The value | `dbt.metric.value` |
| `anomaly_engine` | text | Engine that scored it. Null while not scored | `dbt.anomaly.engine` |
| `anomaly_expected`, `anomaly_lower`, `anomaly_upper`, `anomaly_score` | float | Band and score. Null while not scored | `dbt.anomaly.expected`, `.lower`, `.upper`, `.score` |
| `anomaly_is_anomaly` | boolean | Verdict. Null while not scored | `dbt.anomaly.is_anomaly` |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `node_unique_id` | text | The node, when the record has one | `dbt.node.unique_id` |
| `emit_query_id` | text | Query id of the `CALL` that wrote the record | `snow.query.id` in `RESOURCE_ATTRIBUTES` |

## `SCHEMA_CHANGES`

One row per detected column change, from `icecore.schema_change` span events. Nothing writes these records yet, so the view is empty until the schema monitor test is merged.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `relation` | text | The relation | `dbt.node.relation` |
| `change_kind` | text | `added`, `removed` or `type_changed` | `dbt.schema.change_kind` |
| `COLUMN` | text | Column name | `dbt.schema.column` |
| `from_type` | text | Previous type. Null for `added` | `dbt.schema.from_type` |
| `to_type` | text | New type. Null for `removed` | `dbt.schema.to_type` |
| `detected_at` | time | When the record was written | event `TIMESTAMP` |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `node_unique_id` | text | The node | `dbt.node.unique_id` |
| `emit_query_id` | text | Query id of the writing `CALL` | `snow.query.id` in `RESOURCE_ATTRIBUTES` |

## `LINEAGE`

One row per dependency edge per invocation, from the `lineage` log records. Edges between models, seeds, snapshots and sources; tests are left out. A run's edges may be split over several records. The view flattens them, so one row is one edge whatever the split. A repeated edge in one invocation is one row. Needs the `lineage` var on (the default).

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `project` | text | Root dbt project | `dbt.project` |
| `parent_unique_id` | text | Upstream node | element 0 of a pair in `dbt.lineage.edges` |
| `child_unique_id` | text | Downstream node | element 1 of a pair in `dbt.lineage.edges` |
| `recorded_at` | time | Time of the edge's latest record | event `TIMESTAMP` |

## `LINEAGE_LATEST`

The same columns as `LINEAGE`, only for the edges of each project's latest invocation that wrote lineage.

## `CAPABILITIES`

What the account supported when icecore last looked. One row per database, the latest detection, from the `icecore.capabilities` events that `setup` and `doctor` write.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `database_name` | text | The database | `icecore.capability.database` |
| `detected_at` | time | When it was detected | event `TIMESTAMP` |
| `edition` | text | `STANDARD`, `ENTERPRISE`, `BUSINESS_CRITICAL`, `VIRTUAL_PRIVATE_SNOWFLAKE` or `UNKNOWN` | `icecore.capability.edition` |
| `edition_source` | text | How the edition was found: `organization_usage`, `database_event_table` or `unknown` | `icecore.capability.edition_source` |
| `event_table` | text | The active event table | `icecore.capability.event_table` |
| `event_table_level` | text | Where it is set, such as `DATABASE` or `ACCOUNT` | `icecore.capability.event_table_level` |
| `cortex_available` | boolean | Whether the Cortex anomaly class is visible | `icecore.capability.cortex` |
| `cortex_reason` | text | Why not, when it is not | `icecore.capability.cortex_reason` |
| `log_level`, `trace_level` | text | The database's levels | `icecore.capability.log_level`, `.trace_level` |
| `icecore_version` | text | Package version that detected it | `icecore.version` |
| `invocation_id` | text | The dbt invocation | `dbt.invocation_id` |

## Keeping this page in step

An offline test, `integration_tests/tests/test_views_reference_docs.py`, renders each view and fails when a column of a view is missing from its section here. Add the column here when it fails.
