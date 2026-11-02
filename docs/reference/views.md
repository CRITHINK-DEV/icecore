# Views reference

`icecore.setup` creates these views in `<target_db>.<obs_schema>` (default `ICECORE_OBS`). They are the public interface: read them, not the event table. Each one starts from the same filter over the event table, `icecore.base_events()`, so records of other tools and other environments are never returned. The filter is on the [conventions page](../conventions.md#the-base-filter).

Column types below are from `DESCRIBE VIEW` on the sandbox. Text columns are `VARCHAR`, times are `TIMESTAMP_NTZ(9)`. A key that a record lacks reads as null and never fails a view.

Every time column is the event's `TIMESTAMP`, because `OBSERVED_TIMESTAMP` is NULL on span and span-event records (spike S3). Records reach the table with a p95 lag of 7.73 s and a worst record at 20.3 s (S3.md). A view read right after a run can miss its last records.

`COLUMN` is a reserved word in Snowflake. Quote it: `"COLUMN"`.

Re-running `icecore.setup` replaces the views with `COPY GRANTS`, so grants you made on them stay.

The Elementary-compatible views are a separate set, in [their own section](#elementary-compatible-views).

## Who can read

| Reader | Needs |
| --- | --- |
| Anyone who queries a view | USAGE on the database and the schema, SELECT on the view |
| The same, when `setup` resolved the account event table `SNOWFLAKE.TELEMETRY.EVENTS` | Also the `EVENTS_VIEWER` application role. The views then read `SNOWFLAKE.TELEMETRY.EVENTS_VIEW` |
| The role that runs dbt | USAGE on the database, the schema and both procedures, and SELECT on the views, which anomaly history and root cause read (`get_metric_history` reads `METRICS`). Also `EVENTS_VIEWER` when the views read the account table. Without these grants, anomaly history quietly reads as too short. Writing needs neither |

```sql
GRANT USAGE ON DATABASE <db> TO ROLE <reader>;
GRANT USAGE ON SCHEMA <db>.ICECORE_OBS TO ROLE <reader>;
GRANT SELECT ON ALL VIEWS IN SCHEMA <db>.ICECORE_OBS TO ROLE <reader>;
GRANT APPLICATION ROLE SNOWFLAKE.EVENTS_VIEWER TO ROLE <reader>;  -- only for the account table
```

Source: `spikes/GATE1.md`, "What setup creates and the grants it needs".

## `INVOCATIONS`

One row per dbt invocation. It joins three records on `dbt.invocation_id`: the `icecore.run_start` event (written at `on-run-start`), the `icecore.invocation` span and the `run_end` log (both written at `on-run-end`). A run whose end never arrived has the start only. Its `status` is `running` when the start landed less than 24 hours ago, and `unfinished` after that, and `ended_at` and the counts are null.

`started_at`, `ended_at` and `duration_s` are dbt's clock. The `TIMESTAMP` of the records is Snowflake's. The view never subtracts one from the other (see [the two clocks](../conventions.md#the-dbt-clock-and-the-snowflake-clock)).

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `runtime` | text | `core`, `cloud` or `fusion` | `dbt.runtime` |
| `command` | text | `build`, `run`, `test`, ... | `dbt.command` |
| `target` | text | dbt target name | `dbt.target` |
| `project` | text | Root dbt project | `dbt.project` |
| `dbt_version` | text | dbt version | `dbt.version` |
| `started_at` | time | When dbt started the run, dbt's clock | `dbt.invocation.started_at` |
| `ended_at` | time | When the run ended, dbt's clock. Null if unfinished | `dbt.invocation.completed_at` |
| `duration_s` | float | Seconds | `dbt.invocation.duration_s` |
| `status` | text | `success`, `warn`, `error`, `unknown`, `running` or `unfinished` | `dbt.invocation.status`, else derived |
| `nodes_total`, `nodes_success`, `nodes_failed`, `nodes_warned`, `nodes_skipped` | number | Node counts. Null if unfinished | `dbt.invocation.nodes_total`, `.nodes_success`, `.nodes_failed`, `.nodes_warned`, `.nodes_skipped` |
| `credits` | float | Sum of the run's node credits: for each node, its latest `cost` record. Null when no node has a cost record (the `cost` var is off, or attribution has not landed yet) | `dbt.cost.credits` of the `cost` records |

## `NODE_EXECUTIONS`

One row per node per invocation. It joins the `icecore.node` span, the `node_result` log and the `icecore.query` events of the node. The row exists as soon as any of the three does. A skipped node has no span, so its `started_at` and `completed_at` come from the log if it has them. `started_at` and `completed_at` are dbt's clock.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `unique_id` | text | The node's dbt unique id | `dbt.node.unique_id` |
| `type` | text | `model`, `seed`, `snapshot`, `test`, ... | `dbt.node.type` |
| `name` | text | Node name | `dbt.node.name` |
| `materialization` | text | `table`, `view`, ... | `dbt.node.materialization` |
| `relation` | text | The quoted `"DB"."SCHEMA"."NAME"`. Null for tests | `dbt.node.relation` |
| `status` | text | dbt's result status | `dbt.result.status` |
| `message` | text | dbt's result message, redacted by default ([`result_message`](../configuration.md#result_message)) and capped with its record at `payload_cap_kb` | `dbt.result.message` |
| `rows_affected` | number | Rows affected | `dbt.result.rows_affected` |
| `execution_time_s` | float | Seconds | `dbt.result.execution_time_s` |
| `started_at`, `completed_at` | time | The node's times, dbt's clock | `dbt.node.started_at`, `dbt.node.completed_at` |
| `query_id` | text | The node's Snowflake query: the result's, else the first query event's | `snow.query.id` |
| `query_ids` | array | Every query id of the node | `snow.query.id` of the `icecore.query` events |
| `credits` | float | The node's credits from its latest `cost` record for this run, so a backfill later replaces an earlier value. Falls back to the result record's credits. Null while attribution has not landed | `dbt.cost.credits` |
| `credits_settled` | text | `no_attribution` when the latest `cost` record closed the node with what was attributed (0 when Snowflake never wrote an attribution row). Null otherwise: the credits are attributed, or may still change | `dbt.cost.settled` |
| `root_cause_unique_id` | text | The upstream node that explains a failed test. Set on the test's row only, and only when the `root_cause` var is on and a cause was found | `dbt.root_cause.unique_id` |
| `root_cause_kind` | text | Why that node: `upstream_failure`, `schema_change` or `stale_source`. Set with `root_cause_unique_id` | `dbt.root_cause.kind` |
| `recorded_at` | time | When the node's result record was written, Snowflake's clock. Null for a node with a span and no result. The silence task compares it with Snowflake's now | event `TIMESTAMP` of the `node_result` log |

## `TEST_RESULTS`

One row per test per invocation. Reads the `node_result` log records of test nodes (unique id starting `test.`). A retried or duplicated record collapses to the latest one per invocation and test.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `invocation_id` | text | The dbt run | `dbt.invocation_id` |
| `project` | text | Root dbt project | `dbt.project` |
| `test_unique_id` | text | The test's dbt unique id | `dbt.node.unique_id` |
| `tested_unique_id` | text | The model or source the test is attached to (dbt's `attached_node`, else the single model, seed, snapshot or source the test depends on). Null for a singular test over several nodes, and for records written before this key existed | `dbt.test.attached_node` |
| `test_name` | text | Test name, such as `not_null` | `dbt.test.name` |
| `test_type` | text | `generic` or `singular` | `dbt.test.type` |
| `COLUMN` | text | Tested column | `dbt.test.column` |
| `severity` | text | `ERROR` (dbt's default) or `WARN` | `dbt.test.severity` |
| `status` | text | `pass`, `fail`, `warn`, `error` or `skipped` | `dbt.result.status` |
| `failures` | number | Failing rows | `dbt.test.failures` |
| `message` | text | dbt's result message, redacted by default ([`result_message`](../configuration.md#result_message)) | `dbt.result.message` |
| `execution_time_s` | float | Seconds | `dbt.result.execution_time_s` |
| `engine` | text | Anomaly engine, when a scored test wrote it | `dbt.anomaly.engine` |
| `expected`, `lower`, `upper`, `score` | float | The anomaly band and score. Null unless a scored test wrote them | `dbt.anomaly.expected`, `.lower`, `.upper`, `.score` |
| `executed_at` | time | When the record was written | event `TIMESTAMP` |
| `query_id` | text | The test's Snowflake query | `snow.query.id` |
| `root_cause_unique_id` | text | The upstream node that explains the failure, when the `root_cause` var is on and a cause was found | `dbt.root_cause.unique_id` |
| `root_cause_kind` | text | Why that node: `upstream_failure`, `schema_change` or `stale_source` | `dbt.root_cause.kind` |


## `METRICS`

One row per metric a monitor computed, from the `icecore.metric` span events that `emit_metric` writes. These tests write them: `icecore.freshness` (`freshness_lag_seconds`), `icecore.volume` (`row_count`), `icecore.column_anomaly`, `icecore.all_columns_anomaly` and `icecore.dimension_anomaly` (the column and dimension metrics they compute). With the `cost` var on, each node whose cost is known also gets a `credits` metric. The `icecore.anomaly` warn events repeat their metric event's keys, so they are not rows here.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `relation` | text | The quoted `"DB"."SCHEMA"."NAME"`. For a test's `credits` metric, the test's unique_id | `dbt.node.relation` |
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

One row per detected column change, from `icecore.schema_change` span events. The `icecore.schema_changes` test writes them, so the view is empty until that test has run on a table whose columns changed.

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

## `SILENCE_EVENTS`

One row per missed run, from the `icecore.silence` span events that the silence task writes through `ICECORE_EMIT`. `icecore.setup` creates the task suspended. The `silence_task_resume` var resumes it. It checks the nodes that have `meta.sla`. Rows exist only once the task has run and found a node whose next run is overdue by more than its grace.

`expected_at` and `last_success_at` are the verdict's own values, UTC to the second, on Snowflake's clock: they come from the `recorded_at` of the node's results. `detected_at` is the `TIMESTAMP` of the verdict record.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `unique_id` | text | The node that did not run | `dbt.node.unique_id` |
| `expected_at` | time | When the run was expected | `icecore.silence.expected_at` |
| `grace` | number | Grace in minutes | `icecore.silence.grace_minutes` |
| `expected_every_minutes` | number | The node's expected interval in minutes | `icecore.silence.expected_every_minutes` |
| `last_success_at` | time | Last successful result of the node | `icecore.silence.last_success_at` |
| `detected_at` | time | When the task wrote the verdict | event `TIMESTAMP` |
| `channel` | text | Alert channel from the node's `meta.alerts` | `icecore.silence.channel` |
| `severity` | text | Severity from the node's `meta.alerts`, `warn` by default | `icecore.silence.severity` |

## `ALERTS_FEED`

One row per incident: every alertable signal in one place. The Snowflake alerts and the app read it, and an acknowledged incident stops notifying. Signals are test failures (`TEST_RESULTS` rows with status `fail`, `error` or `warn`), anomalies (`METRICS` rows with `anomaly_is_anomaly`) and schema changes (`SCHEMA_CHANGES` rows). Missed runs and cost regressions have their place in the view but write no rows yet.

An incident groups the same signal on the same node inside one window: `incident_key` is the md5 of the node key, the kind and the window start. The window is the `alert_window_hours` var, 24 hours by default, aligned to the epoch, so the next window is a new incident and alerts again. Acknowledgements are `icecore.ack` span events written by the `ICECORE_ACK` procedure (`icecore.acknowledge` calls it); only those written by that procedure, at or after the incident's first detection, count.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `incident_key` | text | md5 of `node_key`, `kind` and the window start | computed |
| `kind` | text | `test_failure`, `anomaly` or `schema_change` | computed |
| `node_key` | text | The test's unique id; `relation\|metric\|column\|dimension` for an anomaly; `relation\|column` for a schema change | computed |
| `window_start` | time | Start of the incident's window | computed |
| `severity` | text | `error` or `warn`, from the latest signal | computed |
| `title` | text | One line describing the latest signal | computed |
| `first_detected_at`, `last_detected_at` | time | First and latest signal in the window | event `TIMESTAMP` |
| `occurrences` | number | Signals in the window | computed |
| `last_invocation_id` | text | The dbt run of the latest signal | `dbt.invocation_id` |
| `acknowledged_at` | time | The earliest acknowledgement, null when none | event `TIMESTAMP` of the `icecore.ack` event |
| `acknowledged_by` | text | Who acknowledged it: the caller's `CURRENT_USER`, recorded by the procedure | `icecore.ack.by` |
| `root_cause_unique_id`, `root_cause_kind` | text | For a test failure, the test's latest root cause; null for other kinds | `dbt.root_cause.unique_id`, `dbt.root_cause.kind` |

## `SLA_DECLARED`

One row per node that declares `meta.sla`, whether or not it ever missed a run. `icecore.setup` writes the list into the view when it runs (the `create_tasks` step, with the silence task), so after changing `meta.sla`, run setup again. With no `meta.sla` in the project the view exists with zero rows. It reads no event: the values are the ones `collect_sla_config` read from the project.

| Column | Type | Meaning | Source key |
| --- | --- | --- | --- |
| `unique_id` | text | The node | `meta.sla` of the node |
| `expected_every_minutes` | number | Expected interval in minutes | `meta.sla.expected_every` |
| `grace_minutes` | number | Grace in minutes, 1 by default and never more than the interval | `meta.sla.grace` |
| `channel` | text | Alert channel | `meta.alerts.channel` |
| `severity` | text | `warn` (default) or `error` | `meta.alerts.severity` |

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

## Elementary-compatible views

`icecore.setup` with `compat: elementary` creates 8 more views in the compat schema, by default `<target_db>.<obs_schema>_ELEMENTARY` (for example `ICECORE_OBS_ELEMENTARY`). Set another name with the `compat_schema` var. The name may contain only letters, digits and underscores. The schema is created if it does not exist, so the role needs CREATE SCHEMA on the database, or an admin creates it first.

```bash
dbt run-operation icecore.setup --args '{compat: elementary}'
```

The views have the relation names, column names, column order and types of the Elementary dbt package release pinned in `macros/compat/ELEMENTARY_VERSION`. They select from the core views above and never from the event table, so each has the row count of its source view. A column icecore has no source for is a typed NULL. Like the core views, they are replaced with `COPY GRANTS`.

| View | One row per | Source view |
| --- | --- | --- |
| `ELEMENTARY_TEST_RESULTS` | test per invocation | `TEST_RESULTS` |
| `DATA_MONITORING_METRICS` | metric | `METRICS` |
| `SCHEMA_COLUMNS_SNAPSHOT` | column change that leaves the column in the table (`added`, `type_changed`) | `SCHEMA_CHANGES` |
| `DBT_INVOCATIONS` | invocation | `INVOCATIONS` |
| `DBT_RUN_RESULTS` | node per invocation | `NODE_EXECUTIONS` |
| `DBT_MODELS` | model that ran (its latest execution) | `NODE_EXECUTIONS`, `LINEAGE_LATEST` |
| `DBT_TESTS` | test that ran (its latest execution) | `NODE_EXECUTIONS`, `TEST_RESULTS` |
| `DBT_SOURCES` | source that is a parent in the latest lineage | `LINEAGE_LATEST` |

Limits, from the macros:

- `SCHEMA_COLUMNS_SNAPSHOT` is a change log, not a snapshot. Elementary appends every current column on every run. Here a column has a row only when it was added or its type changed, so "the latest snapshot per table" returns only the columns of the latest change.
- icecore holds no manifest metadata. `DBT_MODELS`, `DBT_TESTS` and `DBT_SOURCES` know only what the run records say, so a node appears once it has run. Description, tags, owner, paths and similar columns are NULL.
- `ELEMENTARY_TEST_RESULTS` has no table or schema name (typed NULLs). Its `model_unique_id` is `tested_unique_id`, which can be a source. Scored tests carry their band in `other` as JSON.
- Times that Elementary stores as text are written as `YYYY-MM-DD HH24:MI:SS` text that Snowflake can cast. The two clocks are never subtracted.

## Keeping this page in step

An offline test, `integration_tests/tests/test_views_reference_docs.py`, renders each core view and fails when a column of a view is missing from its section here. Add the column here when it fails.
