# Macro reference

<!-- Generated from the .yml files next to the macros by integration_tests/tests/helpers/reference_docs.py. Edit the .yml, then regenerate. -->

Every public macro of icecore, that is every macro without a leading underscore. `dbt docs generate` shows the same descriptions. Macros that start with `_icecore_` are internal and not listed. The generic tests are on the [tests page](tests.md), the views on the [views page](views.md).

Call a macro in a hook or a model as `{{ icecore.<name>(...) }}`, or from the command line as `dbt run-operation icecore.<name> --args '{arg: value}'`. Each entry shows the call with its required arguments.

## Setup and checks

Run `setup`, `doctor`, `estimate_telemetry_cost` and `set_telemetry_levels` with `dbt run-operation icecore.<name>`. The others are steps they call.

### `capabilities_vars_block`

The `vars:` block for `dbt_project.yml` that matches a detection result: `obs_schema` (the current var, default ICECORE_OBS), `anomaly_engine` (`auto` when Cortex is available, `native` otherwise) and `shadow_engine: none`, with a comment on each choice. No query.

```sql
{{ icecore.capabilities_vars_block(capabilities) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `capabilities` | dict | required | The result of `detect_capabilities`. |

### `create_emit_procedures`

Create (CREATE OR REPLACE ... COPY GRANTS, EXECUTE AS OWNER) the persisted procedures icecore writes through (`ICECORE_EMIT`, `ICECORE_EMIT_RUN`, `ICECORE_ACK`, `ICECORE_NOTIFIED`) and `ICECORE_SCORE`, in `<target.database>.<obs schema>` (spikes/GATE1.md, option B). COPY GRANTS keeps the grants made on them when setup runs again. Called by `setup`; the schema must exist, and the role needs CREATE PROCEDURE on it. The procedures write with the owner's rights; the role that runs dbt needs USAGE on the database, the schema and the procedures it calls (`ICECORE_SCORE` is the only access it needs to Cortex: no grant on a model); USAGE on `ICECORE_ACK` and `ICECORE_NOTIFIED` is granted separately (the latter only to the role that owns the alerts). `ICECORE_EMIT(events ARRAY)`: LANGUAGE SQL. One SPAN_EVENT per element `{name, attrs}` through `SYSTEM$ADD_EVENT`; a name without the `icecore.` prefix gets it. Refuses a CALL of more than 100 events (returns `icecore-error too_many_events: ...` and writes nothing). Returns `ok <n>`. `SCOPE` is `global`. `ICECORE_EMIT_RUN(spans ARRAY)`: LANGUAGE PYTHON 3.11, `PACKAGES = ('snowflake-snowpark-python', 'opentelemetry-api==1.38.0')` (pinned: unpinned, opentelemetry-api resolved to 1.45.1 and failed to import; `snowflake-telemetry-python` must not be added, S1.md). Each element is a span `{name, start_ns, end_ns, attrs, events: [{name, ts_ns, attrs}]}`, written with its explicit times, or a log `{type: 'log', level, message, attrs}` (levels `debug`, `info`, `warn`, `error`), written as a LOG record with attributes. `SCOPE` is `icecore`. Returns `ok spans=<n> logs=<n>`. Callers keep each CALL under 512 KB. `ICECORE_ACK(incident_key STRING)`: LANGUAGE SQL, the only writer of `icecore.ack`. Refuses a key that is not a current unacknowledged incident of the `ALERTS_FEED` view (returns `icecore-error not_an_open_incident: ...`), else writes one span event `icecore.ack` with `icecore.ack.incident_key` and `icecore.ack.by`, the caller's `CURRENT_USER()`. Returns `ok 1`. `ICECORE_EMIT` refuses any event named `icecore.ack` (`icecore-error forbidden_event: ...`, nothing written). `ICECORE_NOTIFIED(incident_key STRING, channel STRING)`: LANGUAGE SQL, the only writer of `icecore.notified`, the record that an alert notified an incident on a channel. Refuses a key that is not 32 hex characters (`icecore-error invalid_incident_key`) and a channel that is not a plain name (`icecore-error invalid_channel`), names no object and takes none, records the caller as `icecore.notified.by`. Returns `ok 1`. `ICECORE_EMIT` and `ICECORE_EMIT_RUN` refuse any event named `icecore.notified` (`icecore-error forbidden_event`, nothing written). `ICECORE_SCORE(model, series, points ARRAY, prediction_interval FLOAT, refresh_days FLOAT)`: LANGUAGE SQL, RETURNS VARIANT. Scores `points` (`{ts, y}`, ts null is now in UTC) of one series with a Cortex anomaly model in the owner's role, the one that trained it (Snowflake documents that models cannot be shared across roles). Returns `{state: missing}`, `{state: stale, age_days}`, `{state: ok, age_days, rows}` (DETECT_ANOMALIES rows, latest first) or `{error}`. Takes no database or schema argument: its own database and obs schema are constants fixed in the body when setup creates it (checked against [A-Za-z0-9_]), so the owner's rights reach only the models of its own schema. Binds its arguments: `model` must match `ICECORE_M_` and 16 upper-case hex digits (checked before any use), the series key is hashed with MD5 (models are trained on that hash), and each point is typed (a timestamp and a finite number) before it is written into the detection input. Records land in the event table of the procedures' database, at that database's levels. Returns the five qualified procedure names (empty at parse time).

```sql
{{ icecore.create_emit_procedures() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema to create them in. Default the `obs_schema` var (see `obs_schema_name`). |

### `detect_capabilities`

Detects what the account supports for icecore, with reads only, each inside a guard so that none can fail the run. The edition comes from SNOWFLAKE.ORGANIZATION_USAGE.ACCOUNTS for the current account. When that view cannot be read, a database with an event table of its own counts as ENTERPRISE (database-level event tables need Enterprise); otherwise the edition is UNKNOWN. The database's `EVENT_TABLE` (and the level it is set at), `LOG_LEVEL` and `TRACE_LEVEL` come from `SHOW PARAMETERS IN DATABASE`. Cortex is available when the class `SNOWFLAKE.ML.ANOMALY_DETECTION` is visible to the role (`SHOW CLASSES`); any error, a missing privilege included, gives false with the reason. Logs a one-line summary and the vars block for `dbt_project.yml` (`capabilities_vars_block`), then records the result in-band as one span event `icecore.capabilities` through `icecore.emit`, with the keys `icecore.capability.database`, `.edition`, `.edition_source`, `.event_table`, `.event_table_level`, `.log_level`, `.trace_level`, `.cortex` (boolean) and `.cortex_reason`. A missing emitter procedure only logs a warning (emit's guard). Returns `{database, edition, edition_source, event_table, event_table_level, log_level, trace_level, cortex, cortex_reason, recorded}`, the dict `set_telemetry_levels` takes as `capabilities`. Returns none at parse time.

```sql
{{ icecore.detect_capabilities() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `record` | boolean | `true` | Default true. Emit the capabilities record; false only detects and prints (`recorded` is none). |
| `database` | string | `none` | The database to detect for. Default `target.database`. A plain identifier is upper-cased, as Snowflake stores it. |
| `cortex_class` | string | `'SNOWFLAKE.ML.ANOMALY_DETECTION'` | The fully qualified class whose visibility means Cortex anomaly detection is available. Default `SNOWFLAKE.ML.ANOMALY_DETECTION`. |

### `doctor`

Checks an icecore install and reports; it never fails the run. `dbt run-operation icecore.doctor`. Re-detects the capabilities (detect_capabilities, reads only), then checks in order: an active event table; the obs schema's levels (`LOG_LEVEL` at least INFO, `TRACE_LEVEL` ALWAYS; the `ALTER SCHEMA` fix is printed, never run); one guarded test `CALL` of `ICECORE_EMIT` and of `ICECORE_EMIT_RUN`, tagged `icecore.doctor.probe = true`, and an informational probe of `ICECORE_ACK` with a key that is no incident (never a failure and never a grant suggestion: the dbt role must not hold it; nothing is written) (an `icecore.doctor_probe` span event and an INFO LOG record), so a missing procedure or grant shows; and the icecore records of the last 24 hours, counted through `base_events` on Snowflake's clock, without doctor's probes and the `icecore.capabilities` records of setup and doctor. Reports `OK: <n> icecore records in the last 24 h`, or `no records in 24 h` with the likely cause: the first failed check (event table, levels, procedures), else the telemetry hooks (`on-run-start: icecore.start_run()` and `on-run-end: icecore.end_run(results)`; on dbt 1.8, `source freshness` fires them only with the `source_freshness_run_project_hooks` flag, spikes/S3.md). Records were found but a check failed, or the count could not be read: status `warn`. Then records the capabilities through `ICECORE_EMIT`, as setup does. Returns `{status, cause, message, records_24h, checks, database, schema, probes, capabilities, recorded}`; status is `ok`, `no_records` or `warn`, and each check is `{name, ok, detail}`.

```bash
dbt run-operation icecore.doctor
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema to check. Default the `obs_schema` var (`ICECORE_OBS`). |

### `estimate_telemetry_cost`

Estimates the rows icecore writes to the event table and the ingestion credits, before telemetry is turned on. `dbt run-operation icecore.estimate_telemetry_cost --args '{runs_per_day: 24}'`; `setup` runs it too. Reads only the dbt graph and runs no query. Rows per run follow the write-path design (spec section 4; spikes/GATE1.md, "Consequences for the build"): one start record, the invocation span, one end LOG, one span per node (models, seeds, snapshots and tests), one node-result LOG per node (only when the `node_results` var is on), `query_events_per_node` query events per node (only when the `query_events` var is on; both counts come from the macro next to end_run's emitter list, so a new emitter cannot be left out unnoticed), the lineage records (only when the `lineage` var is on: the graph's edges split under the payload cap, as `emit_lineage` splits them), one sample per failing test with `store_failures` (only when the `samples` var is on, which is not the default), plus the rows the procedures write themselves: one SPAN per CALL (spikes/S3.md, finding 4) and about 3 Snowpark LOG rows per `ICECORE_EMIT_RUN` CALL (spikes/S1.md, "Python noise"), with `ICECORE_EMIT_RUN` CALLs of at most 512 KB. S3 measured ingestion lag, not volume. Credits use Snowflake's Service Consumption Table (effective October 29, 2026), Table 5: Telemetry Data Ingest, 0.0212 Platform Credits per GB. The range runs from `bytes_per_record` per row to the payload cap per row, with GB taken as 10^9 bytes. Until Telemetry Data Ingest replaces Logging on an account, Logging is billed instead (1.25 x serverless compute plus 0.28 credits per 1000 file batches), which a row count cannot predict; the message says to check `METERING_HISTORY` for the service types `TELEMETRY_DATA_INGEST` and `LOGGING`. Monitor metrics are not included. Without a usable `runs_per_day` it prints the rows per run, asks for `runs_per_day` and does not fail. Returns `{status, nodes, runs_per_day, days, records_per_run, rows_per_run, emit_run_calls, rows_per_month, gb_per_month, credits_per_month, credits_per_gb, message}`; status is `ok` or `needs_runs_per_day`.

```bash
dbt run-operation icecore.estimate_telemetry_cost
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `runs_per_day` | number | `none` | dbt runs per day that fire the hooks. Required for the monthly figures; missing, zero or not a number, the estimate asks for it. |
| `nodes` | integer | `none` | Nodes per run. Default the models, seeds, snapshots and tests of the graph. |
| `query_events_per_node` | integer | `1` | Queries per node, one query event each. Default 1. |
| `failing_tests_per_run` | integer | `0` | Failing tests per run with `store_failures` that send a sample; counted only when the `samples` var is on. Default 0 (warn-severity tests send none unless `samples_on_warn` is on). |
| `days` | integer | `30` | Days per month. Default 30. |
| `bytes_per_record` | integer | `1500` | Bytes per row for the low end of the range. Default 1500: icecore rows measured in the sandbox on 31 Oct 2026 had a median of 1.1 to 1.5 KB serialized. |

### `obs_schema`

The obs schema fully qualified and quoted, `"<TARGET_DATABASE>"."<OBS_SCHEMA>"` (both parts upper-cased).

```sql
{{ icecore.obs_schema() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | Overrides the `obs_schema` var, as in `obs_schema_name`. Default none. |

### `obs_schema_name`

The name of icecore's schema in the target database, upper-cased as Snowflake resolves an unquoted identifier: the `schema` argument when given (setup's and doctor's), else `var('icecore').obs_schema`, default `ICECORE_OBS`. The procedures and views live there, and the views filter `snow.schema.name` on it.

```sql
{{ icecore.obs_schema_name() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | Overrides the var. Default none. |

### `schema_levels`

The `LOG_LEVEL` and `TRACE_LEVEL` effective on the obs schema (`SHOW PARAMETERS LIKE '%LEVEL' IN SCHEMA`: the schema's own value, else the database's), as `{log_level, trace_level}`. Returns none when the schema cannot be read (it does not exist yet, or the role has no USAGE on it) and at parse time. Read only, inside a guard.

```sql
{{ icecore.schema_levels(database) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `database` | string | required | The target database. |
| `schema` | string | `none` | The obs schema. Default the `obs_schema` var (`ICECORE_OBS`). |

### `set_telemetry_levels`

Makes sure icecore's records are captured, without ACCOUNTADMIN for normal use (spikes/GATE1.md, "Prerequisites" and open item 7). Levels belong to the schema that holds the `ICECORE_EMIT` procedures (the obs schema), never to the session, the account or the whole database: `LOG_LEVEL` at least INFO and `TRACE_LEVEL` ALWAYS, effective on that schema (set there or inherited). `ON_EVENT` is not enough: it lands the span events of `ICECORE_EMIT` but drops the explicit spans that `ICECORE_EMIT_RUN` writes without an event (measured 1 Nov 2026, `spikes/results/gate1_followups.json`). When the levels are sufficient nothing is changed. Otherwise the one statement icecore may run, `ALTER SCHEMA <db>.<schema> SET LOG_LEVEL = 'INFO'` and/or `TRACE_LEVEL = 'ALWAYS'` (only the missing ones), is tried inside a guard; it needs `MODIFY LOG LEVEL` and `MODIFY TRACE LEVEL` on the account, so when the role lacks them it is printed for an admin and the run goes on. icecore never proposes `ALTER DATABASE ... SET TRACE_LEVEL`, which would trace every procedure and UDF in the database. Everything else is printed, never run: `ALTER ACCOUNT SET EVENT_TABLE` when no event table is active (ACCOUNTADMIN), and, on Enterprise or above when a dedicated table is requested, `CREATE EVENT TABLE`, its clustering key and `ALTER DATABASE <db> SET EVENT_TABLE`. On Standard a dedicated table is not available and the account's active table is used. A custom event table gets the recommended clustering key `(TO_DATE(TIMESTAMP), RECORD_TYPE)`; when the views read the account table, a note says readers need the `SNOWFLAKE.EVENTS_VIEWER` application role. Returns `{database, levels_ok, executed, printed, notes}` and logs every line as `icecore: ...`.

```bash
dbt run-operation icecore.set_telemetry_levels --args '{capabilities: <capabilities>}'
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `capabilities` | dict | required | The detection result (`icecore.detect_capabilities()`), required. Keys read: `edition` (STANDARD, ENTERPRISE, BUSINESS_CRITICAL, VIRTUAL_PRIVATE_SNOWFLAKE or UNKNOWN), `event_table` (the database's EVENT_TABLE parameter, empty when none is active), `log_level`, `trace_level` (the database's effective values, used when `schema_levels` is not given) and optionally `database`. |
| `database` | string | `none` | The target database. Default `capabilities.database`, then `target.database`. |
| `dedicated_event_table` | string | `none` | Fully qualified name of a dedicated database-level event table to use (Enterprise or above). Default none: use the active event table. |
| `apply` | boolean | `true` | Default true: try the schema's level statement (guarded). False: print it instead. No other statement is ever run. |
| `schema` | string | `none` | The obs schema whose levels are checked and set. Default the `obs_schema` var (`ICECORE_OBS`). |
| `schema_levels` | dict | `none` | `{log_level, trace_level}` effective on the schema, as `icecore.schema_levels()` reads them. Default none: the database's levels from `capabilities` are checked. |

### `setup`

Installs icecore in one command: `dbt run-operation icecore.setup --args '{schema: ICECORE_OBS, compat: none, app: false, runs_per_day: 24}'`. Runs one ordered list of steps: detect_capabilities (reads only), `CREATE SCHEMA IF NOT EXISTS` for the obs schema, create_emit_procedures (`ICECORE_EMIT`, `ICECORE_EMIT_RUN`, `ICECORE_ACK`, `ICECORE_NOTIFIED` and `ICECORE_SCORE`, `CREATE OR REPLACE ... COPY GRANTS`, so grants made by an admin survive a re-run), set_telemetry_levels (the database's levels only; what the role cannot run is printed for an admin), records the capabilities as an `icecore.capabilities` span event through the new `ICECORE_EMIT`, then prints the telemetry cost estimate (`estimate_telemetry_cost`) and creates the views (`create_views`: `CREATE OR REPLACE VIEW ... COPY GRANTS` over `icecore.base_events()`, so grants made on them survive a re-run). With `compat: elementary` it then creates the Elementary-compatible views in their own schema (`create_compat_views`), then creates the tasks (`create_tasks`, one list, created suspended: the silence task when a model declares meta.sla, and the Cortex model refresh task only when Cortex is available, with the grant the dbt role needs printed). Then create_alerts creates one suspended ALERT per channel of var `icecore.alert_channels` (nothing when none is configured) and prints the notification integrations and grants an admin must run; it needs CREATE ALERT on the schema. The step that does not exist yet (deploy_app) keeps its place in the list and is reported as planned. Idempotent: a second run leaves the schema, the procedures' and views' DDL and their grants as they were, and changes no level that is already sufficient. Creates no tables. When `schema` differs from the `obs_schema` var, a warning says to set the var, because dbt runs write through the procedures of the var's schema. The role needs USAGE on the target database, CREATE SCHEMA on it (or an existing schema it can use) CREATE PROCEDURE and CREATE VIEW on the schema (and CREATE TASK for the silence task; its procedure `ICECORE_SILENCE_CHECK` runs with its owner's rights, so grant USAGE on it to nobody but the task's owner: the dbt role never needs it), and read access to the event table the views read (the `EVENTS_VIEWER` application role when it is the account table) (spikes/GATE1.md, "What setup creates and the grants it needs"). Returns `{database, schema, compat, app, capabilities, procedures, alerts, levels, recorded, estimate, steps}`, each step `{name, status, detail}` with status `done` or `planned`.

```bash
dbt run-operation icecore.setup
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema in the target database. Default the `obs_schema` var (`ICECORE_OBS`). Upper-cased as Snowflake resolves an unquoted identifier. |
| `compat` | string | `'none'` | Default `none`: no compat views. `elementary` adds the `create_compat_views` step, which creates the Elementary-compatible views in the compat schema (var `icecore.compat_schema`, default the obs schema name plus `_ELEMENTARY`). Any other value is refused before any step runs. |
| `app` | boolean | `false` | Default false. True adds the deploy_app step, reported as planned until the app can be deployed. |
| `runs_per_day` | number | `none` | dbt runs per day, for the telemetry cost estimate. Default none: the estimate prints the rows per run and asks for it. |

### `telemetry_levels_plan`

The pure part of `set_telemetry_levels`: what to do for a detection result, with no query. Returns `{database, schema, log_level, trace_level, levels_ok, attempt, print, notes}`, where `attempt` only ever holds the obs schema's `ALTER SCHEMA ... SET LOG_LEVEL / TRACE_LEVEL` and `print` holds the statements for an admin.

```sql
{{ icecore.telemetry_levels_plan(capabilities) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `capabilities` | dict | required | As for `set_telemetry_levels`. |
| `database` | string | `none` | As for `set_telemetry_levels`. |
| `dedicated_event_table` | string | `none` | As for `set_telemetry_levels`. |
| `schema` | string | `none` | As for `set_telemetry_levels`. |
| `schema_levels` | dict | `none` | As for `set_telemetry_levels`. |

## Run telemetry

The hooks and the emitters behind them.

### `base_attributes`

Invocation-level attributes that go on every icecore record, as a dict of typed values. Each `CALL` of the emitter procedures is its own trace (spikes/GATE1.md), so views and the app group a run on `dbt.invocation_id`, never on `TRACE`. Keys: `dbt.invocation_id` (string, dbt's `invocation_id`); `dbt.runtime` (string: `fusion` when dbt is 2.x, `cloud` when `DBT_CLOUD_RUN_ID`, `DBT_CLOUD_PROJECT_ID`, `DBT_CLOUD_JOB_ID` or `DBT_CLOUD_ENVIRONMENT_ID` is set, `snowflake_native` inside `EXECUTE DBT PROJECT` (invocation `project_dir` is `/tmp/dbt` and `profiles_dir` starts with `/tmp/dbt_effective_profiles`, measured in spike S13; a heuristic on paths Snowflake does not document, if they change the value falls back to `core`), else `core`); `dbt.command` (string, `flags.WHICH`: `build`, `run`, `test`, `run-operation`, ...); `dbt.target` (string, `target.name`); `dbt.project` (string, the root project's name); `icecore.version` (string, the installed package's `version` from its dbt_project.yml, read from dbt's runtime config; `unknown` when the runtime does not expose it); `icecore.schema_version` (number, the record layout version, currently 1; views use it to read older records). Renders without a connection.

```sql
{{ icecore.base_attributes() }}
```

This macro takes no arguments.

### `collect_query_ids`

The Snowflake query ids behind one dbt run result, read from `result.adapter_response.query_id` (a string, or a list where an adapter reports several). Empty values are dropped and duplicates kept once, in order. dbt's adapter response holds the id of the node's main statement, so a node usually has one id. Returns a list of strings, empty when the result has none (a skipped node, or an adapter response without an id).

```sql
{{ icecore.collect_query_ids(result) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `result` | run result | required | One of dbt's on-run-end `results`, or a dict shaped like a run_results.json result. |

### `emit`

Write one icecore record to the event table, safely. The record is a SPAN_EVENT named `icecore.<kind>` written by `CALL`ing `<target db>.<obs schema>.ICECORE_EMIT` (spikes/GATE1.md, option B). SQL cannot set `SCOPE` (it is `global`), so the `icecore.` name prefix is the package identity. Attributes: `base_attributes()` (including `dbt.invocation_id`), then the caller's `attributes`, then `icecore.level` and `icecore.message`. A record larger than `payload_cap_kb` (default 64, clamped to 1..256, measured on the JSON sent) has its longest string values cut and carries `icecore.truncated = true` and `icecore.truncated_keys` (comma-separated). The CALL runs inside the guard block measured in S1: a missing procedure, schema or grant returns `icecore-error <code>: <message>`, which is logged as `icecore: warning: telemetry not written (...)`, and the run continues. Many failing CALLs of one call to `emit_events` log one warning, with the count. Inputs are validated: an event that is not a mapping is skipped, attributes that are not a mapping are ignored, and every value is cleaned, nested ones too: a number from `run_query` (a Decimal) is written as an int or a float, a date or datetime as ISO text, and anything else as text, so none raises. `SYSTEM$ADD_EVENT` itself silently drops mapping and list attribute values (measured 1 Nov 2026), so `emit` writes a mapping or list value as a JSON string (the attribute lands as JSON text). `emit_run` writes them as JSON text on spans and span events too, and keeps native nesting only on LOG records. Chunks are measured on the escaped literal that is sent. What is left: the `run_query` of the guard block itself can still raise (no warehouse, a closed connection), and dbt's Jinja cannot catch it. Never INSERTs, never emits from an anonymous block, never changes session levels; the database's LOG_LEVEL and TRACE_LEVEL apply. A no-op at parse time. Returns `{events, calls, statuses}` (one status per CALL: `ok <n>` or `icecore-error ...`). Event-table timestamps are the CALL's, so dbt timings belong in attributes.

```sql
{{ icecore.emit(kind) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `kind` | string | required | Record kind; the record name is `icecore.<kind>` (a kind already starting with `icecore.` is kept as is). |
| `message` | string | `none` | Optional human-readable text, stored as the `icecore.message` attribute (span events have no VALUE). Default none. |
| `attributes` | dict | `none` | Optional attributes, keyed in the spec section 4 namespace (`dbt.*`, `icecore.*`). Values should be strings, numbers or booleans; types are kept. Default none. |
| `level` | string | `'info'` | `info`, `warn` or `error`, stored as `icecore.level`. Default `info`. |

### `emit_events`

Write many records as `emit` does, in as few CALLs as possible: chunks of at most 100 events and 512 KB of JSON per CALL of ICECORE_EMIT (S2: a CALL keeps only its last 128 events, and lands none at about 1 MB; one CALL per event costs about 100 times the server time, S1). Each CALL is guarded. Returns `{events, calls, statuses}`.

```sql
{{ icecore.emit_events(events) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `events` | list of dict | required | Each `{kind, message, attributes, level}`, with the meanings and defaults of `emit`. |

### `emit_lineage`

End-of-run emitter `lineage` of `end_run`, on when `var('icecore').lineage` is true (the default). Adds LOG records with `icecore.kind = lineage` that carry the project's DAG edges, written through ICECORE_EMIT_RUN. An edge is a [parent, child] pair of unique ids read from `graph.nodes` `depends_on`, between models, seeds, snapshots and sources. Tests are left out, because a test is not a lineage edge. The whole current graph is sent on every run, not only the selected nodes. `dbt.lineage.edges` is a JSON list of the pairs, sorted. Edges that do not fit `payload_cap_kb` are split across as many records as needed, in order, with `dbt.lineage.part` and `dbt.lineage.parts`, `dbt.lineage.edge_count` (edges in this record) and `dbt.lineage.edges_total`. No edge is dropped and no lineage record is truncated. A graph without edges adds no record. Returns nothing.

```sql
{{ icecore.emit_lineage(results, batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | required | dbt's on-run-end `results`. Not read; the edges come from the graph. |
| `batch` | dict | required | `end_run`'s batch: `items` is appended to. |

### `emit_node_results`

End-of-run emitter `node_results` of `end_run`, on when `var('icecore').node_results` is true (the default). Adds one LOG record per node to the run's batch, written through ICECORE_EMIT_RUN: `icecore.kind = node_result`, `node_attributes(node, result)` (`dbt.node.*`, `dbt.result.status`, `rows_affected`, `execution_time_s`, `message`, `dbt.test.*` with `dbt.test.failures` for tests, and `snow.query.id`), and, when the node has a span, `dbt.node.started_at`, `completed_at` and `duration_s`. The level is error for error, fail and runtime error, warn for warn and partial success, info otherwise; the message is `<unique_id> <status>: <dbt message>`. Returns nothing.

```sql
{{ icecore.emit_node_results(results, batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | required | dbt's on-run-end `results`, hook operations already left out by `end_run`. |
| `batch` | dict | required | `end_run`'s batch: `items` (appended to) and `node_spans` (read for the timings). |

### `emit_query_events`

End-of-run emitter `query_events` of `end_run`, on when `var('icecore').query_events` is true (the default). For each query id of a node (`collect_query_ids`), adds one span event `icecore.query` to that node's `icecore.node` span, with `snow.query.id` and `dbt.node.unique_id`, timed at the span's end. The id is the node's own query, which cost attribution joins to QUERY_ATTRIBUTION_HISTORY; the record's RESOURCE_ATTRIBUTES `snow.query.id` is the emitting CALL's. A node without a span (no timing) gets no event. Returns nothing.

```sql
{{ icecore.emit_query_events(results, batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | required | dbt's on-run-end `results`, hook operations already left out by `end_run`. |
| `batch` | dict | required | `end_run`'s batch: `node_spans`, whose `events` lists are appended to. |

### `emit_run`

Write a run's spans and LOG records with `CALL`s of `<target db>.<obs schema>.ICECORE_EMIT_RUN` (spikes/GATE1.md, option B; SCOPE `icecore`). Items use the procedure's shapes: a span is `{name, start_ns, end_ns, attrs, events}`, each event `{name, ts_ns, attrs}`, written with these explicit times (nanoseconds since the epoch); a log is `{type: log, level, message, attrs}`. Every record and every span event gets `base_attributes()` (so `dbt.invocation_id` is on each), then its own attributes, capped at `payload_cap_kb` as `emit` caps them; a log message counts towards its record's cap and, when cut, is flagged as `icecore.message` in `icecore.truncated_keys`. Records go in as many CALLs of at most 512 KB of JSON as needed, with no count limit (S2), each inside the guard block, so a missing procedure or grant only logs a warning, one per call to `emit_run` (with the number of failing CALLs). Mapping and list attribute values are kept native on LOG records but written as JSON text on spans and span events, which Snowflake drops them from silently otherwise. Items that are not a mapping, and spans without a numeric `start_ns`, are skipped instead of failing the whole CALL. Each CALL is its own trace. A no-op at parse time. Returns `{records, calls, statuses}` (one status per CALL, `ok spans=<n> logs=<n>` or `icecore-error ...`).

```sql
{{ icecore.emit_run(items) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `items` | list of dict | required | Spans and logs in the shapes above. |

### `emit_samples`

End-of-run emitter `samples` of `end_run`, on only when `var('icecore').samples` is true (default false: samples copy row values into the event table, which may be readable by more people than the data). For each test that failed (status fail; also warn when `var('icecore').samples_on_warn` is true, default false) and whose failures dbt stored (`store_failures`), reads up to `var('icecore').sample_rows` rows (default 10, clamped to 1..1000) of the failures relation and adds one LOG record at INFO with `icecore.kind = sample` to the run's batch, written through ICECORE_EMIT_RUN. The rows are read by a guarded query: a Snowflake Scripting block that catches every error, so a missing table or a privilege problem logs a warning and skips that sample and never fails the run. The record carries the test's `node_attributes` (`dbt.node.*`, `dbt.test.*`, `dbt.result.*`), `dbt.sample.rows` (the rows as a JSON list of objects, NULLs kept), `dbt.sample.row_count` and `dbt.sample.truncated`. Rows are dropped from the end until the record fits `payload_cap_kb`, and `dbt.sample.truncated` is then true. Columns named in `var('icecore').sample_exclude_columns` (a list or comma-separated string, case-insensitive, default none) keep their name in every row and their value becomes `?`; the masking is done in the query, so the value never reaches dbt. A test without stored failures, one that errored, or one that passed gets no sample. Samples contain real data from your tables, so anyone who can read the event table can read them; they stay off until you set `samples` to true. Returns nothing.

```sql
{{ icecore.emit_samples(results, batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | required | dbt's on-run-end `results`, hook operations already left out by `end_run`. |
| `batch` | dict | required | `end_run`'s batch: `items` is appended to. |

### `end_run`

The on-run-end hook of run telemetry. Add it to dbt_project.yml as the on-run-end hook calling icecore.end_run with dbt's `results` (README, "Try it in 60 seconds"). One orchestrator: it builds the run's batch and sends it with `icecore.emit_run` to ICECORE_EMIT_RUN, in as many guarded CALLs of at most 512 KB as needed. The batch holds, in order, the invocation span `icecore.invocation` (from `run_started_at` to the hook, with `dbt.invocation.started_at`, `dbt.invocation.completed_at` and `dbt.invocation.duration_s`); one span `icecore.node` per node that has timing, with the node's keys and its start and end taken from dbt's `result.timing` (first start, last end), also written as `dbt.node.started_at`, `dbt.node.completed_at`, `dbt.node.duration_s` and `dbt.timing.<phase>.started_at` / `completed_at`; the items of each end-of-run emitter whose var is on (`node_results`: `emit_node_results`, `query_events`: `emit_query_events` and `lineage`: `emit_lineage`, all on by default, and `samples`: `emit_samples`, off by default (opt-in, it copies row values); cost and root cause are to be added to the same list); then the invocation-end LOG record (`icecore.kind = run_end`), which carries `dbt.invocation.status` (`error` when a node errored or failed, else `warn` when one warned, else `success`), `dbt.invocation.nodes_total`, `nodes_success`, `nodes_failed` (error, fail, runtime error), `nodes_warned` (warn, partial success) and `nodes_skipped`, plus `dbt.version`, the engine settings and the invocation's times; its level is error, warn or info to match. Span times are dbt's, never ingestion time, and node spans are not nested under the invocation span (nesting across CALLs is unmeasured, GATE1.md open item 6). Every record carries `dbt.invocation_id`; each CALL is its own trace, so a run is grouped by that attribute. Failure isolation: an end-of-run emitter that returns an error (a mapping with an `error` key, as one whose guarded query failed does) or whose list entry is unusable is skipped, the next emitter still runs, and one warning (`icecore: warning: end_run emitters reported errors ...`) names them. A failing CALL logs one warning per hook. dbt's Jinja has no try and except, so an exception raised inside an emitter's own Jinja would still stop the hook: emitters only build data and validate their inputs, and run every query inside a guard. Returns an empty string, so the hook runs no SQL of its own. A no-op at parse time and when `var('icecore').telemetry` is false.

```sql
{{ icecore.end_run() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | `none` | dbt's on-run-end `results` (run results with their nodes). Default none, read as no nodes. |

### `node_attributes`

Attributes of one dbt node and, optionally, of its run result, as a dict of typed values: numbers stay numbers (numeric strings are parsed), booleans stay booleans, and a missing value is left out rather than written as null. Accepts graph dicts and dbt objects alike, so it works on `graph.nodes` entries and on the `results` of `on-run-end`. Keys: `dbt.node.unique_id`, `dbt.node.type` (resource type), `dbt.node.name`, `dbt.node.materialization` (`config.materialized`); `dbt.node.relation`, the fully quoted `"DB"."SCHEMA"."NAME"` of models, seeds, snapshots and sources (sources use their `identifier`, others their `alias`), for joins to metadata views. Unquoted parts are upper-cased as Snowflake resolves them, quoted parts (source `quoting:` or model `config.quoting`) keep their case, and embedded double quotes are doubled. Absent for tests and other node types. For tests: `dbt.test.name` (the generic test's name, or the node name for a singular test), `dbt.test.type` (`generic` or `singular`), `dbt.test.column` (`column_name`, when set), `dbt.test.severity` (`config.severity`). With a result: `dbt.result.status` (string), `dbt.result.rows_affected` (number, from the adapter response), `dbt.result.execution_time_s` (number, seconds), `dbt.result.message` (string, as `var('icecore').result_message` says: `redacted` by default, where every single-quoted literal becomes '?' and every double-quoted one "?", cut at 1,000 characters, because dbt's message can quote data values from a Snowflake error; `full` keeps dbt's text; `none` leaves the key out), `dbt.test.failures` (number) and `snow.query.id` (the node's query id from the adapter response; the `RESOURCE_ATTRIBUTES` key of the same name holds the emitting `CALL`'s id instead). Combine with `base_attributes()` for a full record.

```sql
{{ icecore.node_attributes(node) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `node` | dict or node object | required | The node: a `graph.nodes` / `graph.sources` entry or a result's `.node`. When none and `result` is given, `result.node` is used. |
| `result` | dict or RunResult | `none` | Optional. A run result (an element of `results` in `on-run-end`, or a dict with `status`, `execution_time`, `message`, `failures` and `adapter_response`). Default none: node keys only. |

### `start_run`

The on-run-start hook of run telemetry. Add it to dbt_project.yml as the on-run-start hook calling icecore.start_run (README, "Try it in 60 seconds"). Writes one record, the span event `icecore.run_start`, through `icecore.emit` (ICECORE_EMIT, inside the guard block, so a missing procedure or grant only logs a warning). The record carries `base_attributes()` (with `dbt.invocation_id` and `dbt.command`), plus `dbt.version`, `icecore.anomaly_engine`, `icecore.shadow_engine` and `dbt.invocation.started_at` (dbt's `run_started_at`, ISO-8601 UTC; the span event itself is stamped with the CALL's time), and the message `dbt <command> started`. Returns an empty string, so the hook runs no SQL of its own. A no-op at parse time and when `var('icecore').telemetry` is false. On dbt 1.8, `dbt source freshness` fires no project hooks unless the behaviour flag `source_freshness_run_project_hooks` is true (spike S3).

```sql
{{ icecore.start_run() }}
```

This macro takes no arguments.

## Alerts

Acknowledging incidents.

### `acknowledge`

Acknowledge an incident of the alerts_feed view. Calls the ICECORE_ACK procedure, which refuses a key that is not a current unacknowledged incident and records the caller (CURRENT_USER) as acknowledged_by itself, so there is no `by` argument. Run it with dbt run-operation icecore.acknowledge. Returns the status, `ok 1` or an `icecore-error` message that is also logged as a warning. The app calls the same macro.

```bash
dbt run-operation icecore.acknowledge --args '{incident_key: <incident_key>}'
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `incident_key` | string | required | The incident_key of an alerts_feed row, 32 hex characters. Required. |

## Alerts, SLAs and tasks

Built by `setup`; rarely called by hand.

### `alert_channels`

The validated channels of var `icecore.alert_channels`, a mapping of name to type (email, slack or teams), integration (the notification integration name) and, for email, recipients; `default` marks the channel that also gets incidents no node claims. Names, integrations and recipients are checked against strict patterns; a bad entry is a compile error naming it. Returns a list sorted by name.

```sql
{{ icecore.alert_channels() }}
```

This macro takes no arguments.

### `alert_integration_sql`

The CREATE NOTIFICATION INTEGRATION statements (email, Slack and Teams webhooks, with placeholders for secrets) and the grants (USAGE on the integration, EXECUTE ALERT on the account, USAGE on the warehouse) an admin runs. A list of SQL texts; icecore only prints them.

```sql
{{ icecore.alert_integration_sql(channels) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `channels` | list | required | The `alert_channels` entries. Required. |
| `schema` | string | `none` | The obs schema holding the webhook secrets. Default the `obs_schema` var. |

### `alert_message_sql`

The SELECT of the notification text for a channel type (email, slack or teams) over a FROM item with the feed's columns. Constant SQL: record values are only ever read as columns. At most 50 incidents, oldest first, cut at 3000 characters. Each line names the kind, the title, the node and, when present, the root cause. For Slack and Teams every field is cleaned of quotes, backslashes and control characters, and Slack's `&`, `<` and `>` are escaped; the lines are joined with ` | `.

```sql
{{ icecore.alert_message_sql(type, source) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `type` | string | required | email, slack or teams. Required. |
| `source` | string | required | The FROM item to read. Required. |

### `alert_name`

The qualified name of a channel's alert, the obs schema then `ICECORE_ALERT_` and the upper-cased channel name.

```sql
{{ icecore.alert_name(channel) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `channel` | mapping | required | An entry of `alert_channels`. Required. |
| `schema` | string | `none` | The obs schema. Default the `obs_schema` var. |

### `collect_sla_config`

Reads the SLA and alert settings that users declare in model YAML, next to the model. Declare them under `config: meta:` (top-level `meta:` raises PropertyMovedToConfigDeprecation on dbt 1.12): `config: {meta: {sla: {expected_every: 1h, grace: 15m}, alerts: {channel: data_platform, severity: warn}}}`. dbt merges `config()` in SQL > YAML > `+meta` in dbt_project.yml into both `node.meta` and `node.config.meta`, so any of those places works. The macro reads `meta` over `config.meta`, which only matters for nodes built by hand. Returns one entry per node that declares `meta.sla` or `meta.alerts`, sorted by `unique_id`: `{unique_id, name, resource_type, expected_every_minutes, grace_minutes, grace_defaulted, channel, severity}`. Without `meta.sla` the three schedule fields are `none`, `none`, `false`; without `meta.alerts` the channel is `none` and the severity `warn`. Durations are whole numbers with a unit `m`, `h`, `d` or `w`, combinable (`15m`, `1h`, `1h30m`, `1d`), and are returned in minutes. `grace` may not be longer than `expected_every`. When `grace` is omitted the default grace applies, capped at `expected_every` and flagged by `grace_defaulted`. The default is meant to be twice the p95 ingestion lag measured by spike S3; until S3 lands it is a fixed placeholder of 10 minutes. Any invalid value raises a compile error that names the node: a bad duration, a missing `expected_every`, a grace longer than `expected_every`, an unknown key under `meta.sla` (allowed: expected_every, grace) or `meta.alerts` (allowed: channel, severity), a severity other than `warn` or `error`, an empty channel, or an `sla` or `alerts` that is not a mapping. Only `graph.nodes` is read: `meta.sla` on sources (`graph.sources`) is ignored. Returns values only: runs no query and renders no SQL; `render_alert_sql` and `render_silence_task_sql` consume the result.

```sql
{{ icecore.collect_sla_config() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `nodes` | list[dict] \| none | `none` | Graph nodes to read (mappings with `unique_id`, `name`, `resource_type`, `meta`, `config.meta`). Default `none`: every node of `graph.nodes`. Returns `[]` at parse time. |

### `cortex_model_name`

Name of a table's Cortex anomaly model: `ICECORE_M_` and the first 16 hex digits (upper case) of the MD5 of the relation, never the relation text. It is the bare name `ICECORE_SCORE` accepts and the `model` argument of `score`. The refresh task computes the same name in SQL and records the relation to model mapping in an `icecore.cortex_model` event. Runs no query.

```sql
{{ icecore.cortex_model_name(relation) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | string \| relation | required | The quoted `"DB"."SCHEMA"."NAME"` the METRICS view carries, or a dbt relation. |
| `schema` | string | `none` | Unused by the name itself (a model always lives in the obs schema); kept for call symmetry. Default none. |

### `cortex_series_key`

The series key `score` takes as `series`: `metric|column|dimension`, with `~` for a missing column or dimension. The model is trained on its MD5, which the refresh task and `ICECORE_SCORE` both compute in SQL, so the key never reaches SQL as text. Runs no query.

```sql
{{ icecore.cortex_series_key(metric) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `metric` | string | required | The metric name, for example `row_count`. |
| `column` | string | `none` | The column the metric is about. Default none, written `~`. |
| `dimension` | string | `none` | The dimension value of the series. Default none, written `~`. |

### `create_alerts`

Create one Snowflake ALERT per channel of var `icecore.alert_channels` in the obs schema (the step `create_alerts` of `setup`), each `CREATE OR REPLACE ALERT`, SUSPENDED, so nothing sends or costs without consent; icecore never resumes an alert. Prints the CREATE NOTIFICATION INTEGRATION statements, the grants and the `ALTER ALERT ... RESUME` statements an admin must run, and never creates an integration. With no channel configured it creates nothing and prints how to configure one. Returns a list of channel, alert and status. The role needs CREATE ALERT on the schema and USAGE on the warehouse (var `icecore.alert_warehouse`, default the target warehouse).

```sql
{{ icecore.create_alerts() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema to create them in, also the schema the feed and the base filter read. Default the `obs_schema` var. |

### `create_refresh_cortex_task`

Creates the task `ICECORE_REFRESH_CORTEX` in the obs schema, in setup's `create_tasks` step, only when Cortex is available. Returns `{task, status, resumed, printed}`: status `ok`, `skipped` (Cortex off, nothing created, no query) or the error of the guarded statement. A new task is created suspended; `cortex_refresh_task_resume: true` resumes it. A task an admin had resumed stays resumed: `SHOW TASKS` runs first, and since `CREATE OR REPLACE` suspends it, it is resumed again when it was started. The schedule is `var('icecore').cortex_refresh_days` (default 7) in whole days from 1 to 8, because Snowflake refuses a schedule above 11520 minutes; any other value stops with a compile error. The warehouse is `cortex_refresh_task_warehouse`, else the target's. `printed` lists what an admin must run: the `GRANT USAGE` on `ICECORE_SCORE` for the role that runs dbt (named by `dbt_role`, else `var('icecore').dbt_role`, else a placeholder; models are never granted), and `CREATE TASK` or `EXECUTE TASK` grants for setup's role when they are missing.

```sql
{{ icecore.create_refresh_cortex_task() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema. Default the `obs_schema` var. |
| `capabilities` | dict | `none` | A `detect_capabilities` result. Default none, which detects (without recording). |
| `warehouse` | string | `none` | Warehouse of the task. Default the `cortex_refresh_task_warehouse` var, else the target's. |
| `resume` | boolean | `none` | Resume a new task. Default the `cortex_refresh_task_resume` var, false. |
| `days` | integer | `none` | Refresh period in days, 1 to 8. Default the `cortex_refresh_days` var, 7. |
| `min_points` | integer | `14` | As for `render_refresh_cortex_models_sql`. Default 14. |
| `dbt_role` | string | `none` | The role that runs dbt, named in the printed `GRANT USAGE`. Default the `dbt_role` var. |

### `create_silence_task`

Creates the procedure `ICECORE_SILENCE_CHECK` (static code) and the Snowflake task `ICECORE_SILENCE` in the obs schema, the `create_tasks` step of `setup`. The task body is `render_silence_task_sql` over the entries of `collect_sla_config`; with no `meta.sla` nothing is created and a line says so. `CREATE OR REPLACE TASK` (a task has no COPY GRANTS, so a re-run resets its grants and leaves it suspended). The task is created suspended, so creating it never starts spending credits without consent; set the var `icecore.silence_task_resume` to true to resume it. Resuming, and `EXECUTE TASK`, need the EXECUTE TASK privilege on the account; when resuming fails the macro logs and returns the statement for an admin, `GRANT EXECUTE TASK ON ACCOUNT TO ROLE <role>`, and the task stays suspended. A role that cannot create the task gets the CREATE TASK grant printed. Vars, defaults in the package `dbt_project.yml`: `silence_task_schedule` (5 MINUTE), `silence_task_warehouse` (default the target's warehouse), `silence_task_resume` (false). Returns `{task, status, resumed, printed}` where status is ok, skipped or the error of the create.

```sql
{{ icecore.create_silence_task() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema to create the task in. Default the `obs_schema` var. |
| `entries` | list[dict] | `none` | The SLA entries. Default `collect_sla_config()`. |
| `source` | string | `none` | The relation read as `node_executions`. Default the obs schema's `NODE_EXECUTIONS` view. |
| `warehouse` | string | `none` | The task's warehouse. Default the `silence_task_warehouse` var, else the target's warehouse. |
| `schedule` | string | `none` | The task's SCHEDULE. Default the `silence_task_schedule` var. |
| `resume` | boolean | `none` | Resume the task after creating it. Default the `silence_task_resume` var (false). |

### `create_sla_declared_view`

Creates the `SLA_DECLARED` view, which lists every model that declares `meta.sla` whether or not it was ever silent. Called by the `create_tasks` step of `setup`; you rarely call it yourself. Does nothing at parse time and returns `skipped`. Otherwise returns `ok`, or the error text when the DDL failed, with one logged warning: it never fails the run.

```sql
{{ icecore.create_sla_declared_view() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema that holds the view. Default the `obs_schema` var (`ICECORE_OBS`). |
| `entries` | list | `none` | The SLA entries. Default none, read from the project with `collect_sla_config`. |

### `create_tasks`

Setup's `create_tasks` step: creates every task of the package, from one list (`_icecore_task_entries`), in order. Today that is the Cortex refresh task (`create_refresh_cortex_task`); with Cortex off its entry is skipped and no task or query is made. Each task is created suspended unless its own var resumes it, and a task an admin had resumed stays resumed. Returns `{tasks, printed}`: `tasks` has one `{name, task, status, resumed}` per entry, `printed` the statements an admin must run. At parse time it returns the empty result.

```sql
{{ icecore.create_tasks() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema. Default the `obs_schema` var. |
| `capabilities` | dict | `none` | A `detect_capabilities` result passed on to each task. Default none, which detects. |

### `dedup_window_minutes`

The dedup window in whole minutes: var `icecore.alert_dedup_minutes` when a positive number, else the incident window of `alerts_feed` (var `icecore.alert_window_hours`, 24 hours by default), so an incident is notified once per channel.

```sql
{{ icecore.dedup_window_minutes() }}
```

This macro takes no arguments.

### `dedup_window_sql`

The SELECT of the incidents a channel's alert may notify now, computed over the `ALERTS_FEED` view and the `icecore.notified` span events (written only by the ICECORE_NOTIFIED procedure, which the alert's action calls; never an INSERT; only records of that executable at or after the incident's first detection count). Columns are those of `ALERTS_FEED` plus `last_notified_at`. An acknowledged incident is never returned. An incident notified on this channel inside the window is not returned again; after the window it is, while unacknowledged (a reminder). A new incident_key has no record and is returned. Another channel's notification does not suppress this one.

```sql
{{ icecore.dedup_window_sql(channel) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `channel` | string | required | The channel name the notifications are recorded under. Required. |
| `schema` | string | `none` | The obs schema holding ALERTS_FEED, also the schema the base filter reads. Default the `obs_schema` var. |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `window_minutes` | integer | `none` | The dedup window in minutes. Default `dedup_window_minutes()`. |
| `channel_sql` | string | `none` | A SQL expression that yields the channel name, used in place of the literal `channel` when the SELECT runs inside an alert that reads the name from its own JSON. Default none: `channel` is quoted as a literal. It is SQL text built by icecore, never a value from a user. |

### `render_alert_sql`

The CREATE OR REPLACE ALERT statement of one channel. Condition is EXISTS over `dedup_window_sql` (new, unacknowledged, not-yet-notified incidents of ALERTS_FEED) filtered to the incidents the channel owns through meta.alerts. The action builds the message in SQL from the condition's result columns (RESULT_SCAN), so a node name or message holding quotes, dollar signs, semicolons or newlines is only data; sends it with SYSTEM$SEND_SNOWFLAKE_NOTIFICATION, naming the model, the kind and the root cause when present; then records each notified incident by calling `ICECORE_NOTIFIED`. At most 50 incidents per run.

```sql
{{ icecore.render_alert_sql(channel) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `channel` | mapping | required | An entry of `alert_channels`. Required. |
| `entries` | list | `none` | The `collect_sla_config` result used for routing. Default read from the graph. |
| `schema` | string | `none` | The obs schema. Default the `obs_schema` var. |
| `event_table` | string | `none` | The relation to read. Default resolved when the macro executes. |
| `warehouse` | string | `none` | The alert's warehouse. Default var `icecore.alert_warehouse`, else the target warehouse. |
| `schedule_minutes` | integer | `none` | The alert's schedule in minutes. Default var `icecore.alert_schedule_minutes`, else 5. |
| `all_channels` | list | `none` | Every configured channel, for the routing. Default `alert_channels()`. |
| `nodes` | mapping | `none` | The nodes (unique_id to node) used to map a model's relation and tests to it for the routing. Default `graph.nodes`. |

### `render_refresh_cortex_models_sql`

The body of the refresh task, one Snowflake Scripting block, as text (no query is run). It reads the obs schema's `METRICS` view over the history window of `get_metric_history` (observed_at after now minus `lookback_days`, up to now minus `history_delay_minutes`, UTC), keeps the series with at least `min_points` points, and creates one `SNOWFLAKE.ML.ANOMALY_DETECTION` model per table, named by `cortex_model_name`, trained on all its series. A failure on one table is reported and does not stop the others. The block ends by setting its result, `ok <n> models, <k> skipped` or `icecore-error ...`, as the task's return value with `SYSTEM$SET_RETURN_VALUE` (a task keeps no RETURN), and writes one `icecore.cortex_model` event per model (relation, model, `icecore.version`) through `ICECORE_EMIT`. The body is constant text: the database and the obs schema must be plain identifiers (letters, digits, underscore) or the macro stops with a compile error, and no value from a record is ever written into it (a relation is checked against the strict `"DB"."SCHEMA"."NAME"` shape, hashed, and passed as a bound value). There is no argument to point it at another view.

```sql
{{ icecore.render_refresh_cortex_models_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema. Default the `obs_schema` var. |
| `lookback_days` | integer | `30` | Days of history to train on, 1 or more. Default 30. |
| `delay_minutes` | integer | `none` | Minutes of the newest history left out, 0 or more. Default the `history_delay_minutes` var. |
| `min_points` | integer | `14` | Points a series needs in the window, 12 or more (Cortex needs 12). Default 14, the engine's own rule. |

### `render_silence_task_sql`

Renders the body of the silence task: one CALL of the procedure `ICECORE_SILENCE_CHECK(sla VARIANT)`, with the SLA entries of `collect_sla_config` as ONE JSON literal, `PARSE_JSON('...')`. No SLA value is part of executed code: the procedure takes only that JSON (the relations it reads and the emitter it calls are fixed and validated in its DDL, so a caller cannot point its owner's rights at another object; grant USAGE on it to nobody but the task's owner), and a `$$`, a quote, a backslash, a semicolon or a newline in a unique_id or a channel stays data inside the escaped literal. The SLA is embedded when setup runs, so change `meta.sla` and run setup again to refresh the task. For each entry with `meta.sla` the procedure takes the latest `recorded_at` of the node's successful rows (status success or pass) in `node_executions`, adds `expected_every`, and flags the node when Snowflake's now is later than that plus the grace (the default grace is 1 minute, from spike S3). `recorded_at` is the TIMESTAMP of the node's result record, so the comparison stays on Snowflake's clock. A node with no successful row yet gets no verdict. A verdict is a span event `icecore.silence`, written by a CALL of `ICECORE_EMIT`, never an INSERT, with `dbt.node.unique_id`, `icecore.silence.expected_at`, `last_success_at`, `expected_every_minutes`, `grace_minutes`, `channel` and `severity` at level warn. A node already in `silence_events` with the same `expected_at` gets none. At most 100 verdicts per run. A failure raises, so the task history shows it. Raises a compiler error when no entry has `meta.sla`.

```sql
{{ icecore.render_silence_task_sql(entries) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `entries` | list[dict] | required | The entries of `collect_sla_config`. Those without `expected_every_minutes` are ignored. |
| `schema` | string | `none` | The obs schema holding `ICECORE_EMIT`, `SILENCE_EVENTS` and `NODE_EXECUTIONS`. Default the `obs_schema` var. |

### `sla_declared_view_ddl`

The `CREATE OR REPLACE VIEW ... COPY GRANTS` statement for the `SLA_DECLARED` view, returned as text and not run. One row per model that declares `meta.sla`, with `unique_id`, `expected_every_minutes`, `grace_minutes`, `channel` and `severity` (default `warn`). Entries without an expected interval are left out. The entries travel as one escaped JSON literal, so `meta.sla` text is data and never SQL. With no entries the view is still created, with zero rows.

```sql
{{ icecore.sla_declared_view_ddl(entries) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `entries` | list | required | The SLA entries, as returned by `collect_sla_config`. A list of dicts with `unique_id`, `expected_every_minutes`, `grace_minutes`, `channel` and `severity`. |
| `schema` | string | `none` | The obs schema that holds the view. Default the `obs_schema` var (`ICECORE_OBS`). |

## Monitor building blocks

Readers and computers the generic tests use.

### `compute_column_metrics`

Computes metrics for one column over the rows of a change window from `get_change_window`, in one query that selects only that column. With a CHANGES window the rows are the INSERT row versions: new rows and the new values of updated rows (deleted rows are not measured). Returns a dict of the requested metrics, for example `{window_rows: 50, null_rate: 0.2}`. Metrics: `window_rows`, the number of rows in the window (named so that it never collides with the volume monitor's `row_count` total or the net `rows_added` of `get_row_count_changes`); `null_rate`, the share of those rows where the column is null, between 0 and 1, none when the window is empty; `avg` as a float, `min` and `max` in the column's own type (a NUMBER stays exact above 2^53 in the returned value only: the metrics view and the native engine cast to float downstream), over the non-null values, none when there are none. `avg`, `min` and `max` need a numeric column (NUMBER, which covers DECIMAL and INT, or FLOAT), read from INFORMATION_SCHEMA.COLUMNS: on any other type they are skipped with one warning and the other metrics are still returned. Unknown metric names are skipped with a warning; when every requested metric is skipped the result is an empty dict and no query runs. Returns none when the window is none. When a CHANGES query fails at run time and the window carries a `timestamp_column`, it runs again over that column (see `get_change_window`); when the query still fails (missing column, lost privilege) it returns none with a logged warning. It never fails the run. Computes and returns values only; it emits nothing.

```sql
{{ icecore.compute_column_metrics(relation, column_name, window) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read, as for `get_change_window`. |
| `column_name` | string | required | The column to measure; resolved as `timestamp_column` is in `get_change_window`. |
| `window` | dict | required | The result of `get_change_window` for the same relation, or none. |
| `metrics` | list | `none` | Optional, default `[window_rows, null_rate]`. The metrics to compute and return, any of `window_rows`, `null_rate`, `avg`, `min`, `max`; only these keys appear in the result. |

### `compute_columns_metrics`

Computes the metrics of several columns of one model in one query, so that a wide table is scanned once instead of once per column (after DML each CHANGES read costs about 1.9 times a full scan, spike S5). Returns a dict with one entry per distinct requested column, keyed as the name was given, each value the dict `compute_column_metrics` returns for that column, or none for a column that is not found (with a warning). Returns none when the window is none or the columns of the relation cannot be read. The column types are read once from INFORMATION_SCHEMA.COLUMNS, then one CHANGES (or timestamp-window) query computes every metric of every column in one select list and the result is split back per column. Numeric metrics (avg, min, max) are skipped with a warning on a column that is not numeric, as in the per-column path. With one distinct column it is the per-column path, unchanged. Window rules are the same as for `compute_column_metrics`; the fallback to `timestamp_column` happens only on SQLCODE 707 or 91930, once per window. Computes and returns values only; it emits nothing.

```sql
{{ icecore.compute_columns_metrics(relation, columns, window) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read, as for `get_change_window`. |
| `columns` | list | required | The columns to measure; each resolved as `timestamp_column` is in `get_change_window`. |
| `window` | dict | required | The result of `get_change_window` for the same relation, or none. |
| `metrics` | list | `none` | Optional, default `[window_rows, null_rate]`. Any of `window_rows`, `null_rate`, `avg`, `min`, `max`, as for `compute_column_metrics`. |

### `compute_dimension_metrics`

Row counts per value of one dimension column (country, segment, ...) over the rows of a change window from `get_change_window`, in one query that selects only that column. With a CHANGES window the rows are the INSERT row versions: new rows and the new values of updated rows. Returns a list of `{value, window_rows, other}` ordered by `window_rows` (rows in the window with that value; named, as in `compute_column_metrics`, so that it never collides with the volume monitor's `row_count` total), highest first: one entry per value for the `max_values` most frequent values (`value` as a string; null is a value of its own), then, when there are more distinct values, one entry `{value: none, window_rows: <sum of the rest>, other: true}`. Returns an empty list when the window has no rows and none when the window is none. When a CHANGES query fails at run time and the window carries a `timestamp_column`, it runs again over that column (see `get_change_window`); when the query still fails (missing column, lost privilege) it returns none with a logged warning. It never fails the run. The cost is that of the window (see `get_change_window`). Computes and returns values only; it emits nothing.

```sql
{{ icecore.compute_dimension_metrics(relation, dimension_column, window) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read, as for `get_change_window`. |
| `dimension_column` | string | required | The column to group by; resolved as `timestamp_column` is in `get_change_window`. |
| `window` | dict | required | The result of `get_change_window` for the same relation, or none. |
| `max_values` | integer | `none` | Optional. How many distinct values are counted one by one before the rest are grouped as `other` (at least 1). Default `vars: icecore: dimension_max_values`, 20. |

### `diff_schema`

Compares two signatures from `get_columns_signature` and returns `{added: [{name, data_type}], removed: [{name, data_type}], type_changed: [{name, from_type, to_type}]}`. Columns are matched by exact (case-sensitive) name, so a rename shows as one removed and one added column. When either signature is none (first observation, or an unreadable relation) all lists are empty: the current signature becomes the baseline. Pure Jinja, no query.

```sql
{{ icecore.diff_schema(previous, current) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `previous` | list | required | The earlier signature, or none. |
| `current` | list | required | The current signature, or none. |

### `emit_metric`

Write one metric, the same way for every monitor (spec section 4). It calls `icecore.emit_events` once, so every record of one `emit_metric` goes in one guarded `CALL` of `ICECORE_EMIT` (spikes/GATE1.md, option B), and a telemetry failure is a logged warning, never a failed run. It writes a span event `icecore.metric` with `base_attributes()` (including `dbt.invocation_id`), `dbt.metric.name`, `dbt.metric.value` (always a JSON number), `dbt.metric.column` and `dbt.metric.dimension` when given, and `dbt.node.relation`, the quoted `"DB"."SCHEMA"."NAME"` as Snowflake resolves it. Only when `anomaly` is given does the record also carry `dbt.anomaly.engine`, `dbt.anomaly.expected`, `dbt.anomaly.lower`, `dbt.anomaly.upper`, `dbt.anomaly.score` and `dbt.anomaly.is_anomaly` (fields that are null are left out). When `is_anomaly` is true, a second span event `icecore.anomaly` at `icecore.level = warn` goes in the same CALL, with the same keys and a message naming the metric, the relation, the value and the band. ICECORE_EMIT writes span events only; LOG records come from ICECORE_EMIT_RUN at on-run-end (GATE1.md), so this event is the anomaly record. A value that is not a number (none, a boolean, a non-numeric string) writes nothing and logs `icecore: warning: emit_metric ...`. Numeric strings and Decimals from `run_query` are written as numbers. A no-op at parse time. Returns what `emit_events` returns, `{events, calls, statuses}`. How a monitor uses it, once per metric it computed - read the value (for example `icecore.get_row_count_metadata(model)`), score it (`icecore.score(history_sql, value)`), then call `icecore.emit_metric(model, 'row_count', value, anomaly=result)`.

```sql
{{ icecore.emit_metric(relation, metric, value) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The monitored relation (`model` in a generic test, `ref()`, `source()` or `api.Relation.create()`); its quote policy is honoured. A string is written as it is. |
| `metric` | string | required | The metric name, such as `row_count`, `rows_added`, `freshness_lag_s`, `null_rate`. |
| `value` | number | required | The metric value. Written as an int or a float; anything else is not written. |
| `column` | string | `none` | The column the metric is about, as `dbt.metric.column`. Default none (key absent). |
| `dimension` | string | `none` | The dimension slice, such as `country=FR`, as `dbt.metric.dimension`. Default none (key absent). |
| `anomaly` | dict | `none` | The anomaly result for this value, as `icecore.score` returns it (`engine`, `expected`, `lower`, `upper`, `score`, `is_anomaly`; other keys are ignored). Default none: no `dbt.anomaly.*` key is written. |
| `attributes` | dict | `none` | Extra attributes written on the metric record (and on the anomaly record). Only keys that start with `dbt.window.` are accepted, for example `dbt.window.until`, the end of the window a column monitor measured; any other key is ignored with a logged warning, so a caller cannot set or replace a metric key, an anomaly key, `dbt.invocation_id` or an `icecore.*` key. Default none. |

### `emit_metrics`

Several metrics of one relation in one `icecore.emit_events` call, so in one guarded CALL of `ICECORE_EMIT` (up to 100 events per CALL, more are chunked), for a monitor that computes many values in a run (one CALL for a test, not one per metric). Each entry is written exactly as `icecore.emit_metric` writes it, including the `anomaly` record and the `dbt.window.*` attribute rule. An entry whose value is not a number is skipped with a logged warning. Returns what `emit_events` returns, `{events, calls, statuses}`. A no-op at parse time.

```sql
{{ icecore.emit_metrics(relation, metrics) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The monitored relation, as for `emit_metric`. |
| `metrics` | list | required | One entry per metric, a mapping with `metric` and `value` and optionally `column`, `dimension`, `anomaly` and `attributes`, as the arguments of `emit_metric`. |

### `get_change_window`

Decides which rows the column monitors read: the rows changed since `since`. Returns `{mode: changes, since}` (plus `timestamp_column` when one is given) when a compile-time probe of CHANGES succeeds; that probe reads no data and catches change tracking being off. It does not prove that change tracking covers the whole window: a table re-created after `since` (dbt full refresh, table models), or one whose change tracking was enabled after rows were written since then, passes the probe and fails only when the CHANGES query runs. `compute_column_metrics` and `compute_dimension_metrics` catch that failure when Snowflake reports it with error code 000707 or 091930, log it, and run again over `timestamp_column` when the window carries one. Any other error is not retried. The window is switched to the timestamp column in place, so later calls with the same window skip CHANGES and the fallback happens once per window. A window belongs to the relation it was made for: never reuse one across relations (a switched window would carry the first relation's fallback). A column that does not exist is reported as not found before any query runs, so it never triggers a second scan over the timestamp column. A `none` column name is a compile error that names the relation. Both paths include rows at `since` (CHANGES AT(TIMESTAMP => since) does, and the timestamp filter is `>=`). A probe that runs would have to read the change data, which is the expensive part. When the probe fails and `timestamp_column` is a DATE or TIMESTAMP column, returns `{mode: timestamp_column, since, timestamp_column, timestamp_type}` and logs why CHANGES was not used. Returns none with a logged warning when neither works (no change tracking and no usable timestamp column, missing relation, `since` missing); it never fails the run. Cost, from spike S5 and this story's sandbox tests: a CHANGES window is cheap only when it holds inserts that landed in new micro-partitions (1 partition and 3 KB against 11 partitions and 38 MB for a full scan). After a DELETE, UPDATE or MERGE (dbt's default incremental strategy) CHANGES reads the old and new versions of every rewritten micro-partition: 18 partitions and 80 MB against 8 and 42 MB for a full scan (1.9 times) after 10 updated rows. On a narrow table a small insert can also be written by rewriting a partly filled micro-partition, and CHANGES then reads that partition twice (2.7 MB against 3.6 MB for a full scan). The timestamp fallback costs whatever the filter prunes, which depends on the table's clustering. The time of the previous run is passed in as `since`: reading it from the metric history comes with the monitors that emit metrics (after Gate 1). `until`, when given, ends the window: CHANGES gets an `end` timestamp and the timestamp-column fallback an upper bound, so rows committed after `until` are left for the next window and a monitor that records `until` as its window end counts no row twice.

```sql
{{ icecore.get_change_window(relation, since) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read (`ref()`, `source()`, `adapter.get_relation()` or `api.Relation.create()`). Its quote policy is honoured: quoted parts keep their case, unquoted parts resolve to upper case. |
| `since` | string | required | ISO 8601 UTC time of the previous observation, for example `2026-10-31T08:00:00.000000Z`. Required. |
| `timestamp_column` | string | `none` | Optional, default none. Column used when CHANGES is unavailable or fails: rows with a value after `since`. A TIMESTAMP_NTZ column is read as UTC wall-clock time. A DATE column has day granularity: the window starts at the day of `since`, so the rows of that day are counted again on every run that day; prefer a timestamp column. Name forms: wrapped in double quotes (`'"amount"'`, `'"Mixed"'`) is the exact name with its case kept (inner `""` stand for one `"`); a plain name (letters, digits, `_`, `$`, such as `amount` or `Mixed`) resolves to upper case like an unquoted identifier; any other name is taken as the exact, case-preserved column name. |
| `until` | string | `none` | Optional, default none. ISO 8601 UTC end of the window: CHANGES `end` and an upper bound on the timestamp column. Rows committed after it belong to the next window. |

### `get_columns_signature`

Zero-scan schema reader. Returns the relation's columns from SHOW COLUMNS as a list of `{name, data_type}`, where `name` is the column name as Snowflake stores it (case preserved) and `data_type` is a normalised type string such as `NUMBER(38,0)`, `VARCHAR(16777216)`, `TIMESTAMP_NTZ(9)`, `FLOAT`, `BOOLEAN`, `VARIANT`. Works on tables and views. Returns none with a logged warning when the relation is missing or not readable; it never fails the run. Column order is not significant: `diff_schema` matches columns by name.

```sql
{{ icecore.get_columns_signature(relation) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read (`ref()`, `source()`, `adapter.get_relation()` or `api.Relation.create()`). Its quote policy is honoured: quoted parts keep their case, unquoted parts resolve to upper case. |

### `get_last_change_time`

Zero-scan freshness reader. Returns the time of the last committed change to the relation, read from SYSTEM$LAST_CHANGE_COMMIT_TIME, as an ISO 8601 UTC string (`YYYY-MM-DDTHH:MI:SS.ffffffZ`, millisecond precision; the token's last digits are not significant). Returns none when the table was never written (token 0), and none with a logged warning when the relation is missing or not readable; it never fails the run. For a dynamic table this is the time of its last refresh, not of its sources (spike S5).

```sql
{{ icecore.get_last_change_time(relation) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read (`ref()`, `source()`, `adapter.get_relation()` or `api.Relation.create()`). Its quote policy is honoured: quoted parts keep their case, unquoted parts resolve to upper case. |

### `get_row_count_changes`

Net number of rows added since `since` (inserts minus deletes), counted over CHANGES. This is not a zero-scan read, and its cost depends on what happened in the window (spike S5). When the window holds only inserts, it reads just the new micro-partitions (1 partition and 4 KB against 11 partitions and 35 MB for a full scan). After a DELETE, UPDATE or MERGE (dbt's default incremental strategy) that rewrites existing micro-partitions, it reads their old and new versions and can cost more than a full scan (about 1.9 times in S5). Needs change tracking on the table, or on the view and its base tables; without it, or for a `since` outside the tracked window, it returns none and logs Snowflake's error. It never fails the run. The volume test emits this value as the metric `rows_added`, never mixed with `row_count`.

```sql
{{ icecore.get_row_count_changes(relation, since) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read, as for `get_last_change_time`. |
| `since` | string | required | ISO 8601 timestamp of the previous observation, for example `2026-10-31T08:00:00.000000Z`. Required. |

### `get_row_count_metadata`

Zero-scan volume reader: the relation's total row count from INFORMATION_SCHEMA.TABLES.ROW_COUNT, as an integer, with no data scan. Always a total, never a delta. Returns none, with one log line saying why, when: the relation is missing or not readable; it is a view or an external table (no ROW_COUNT in metadata, so zero-scan volume is not supported); or its kind is in `vars: icecore: metadata_fallback_kinds` (default `[iceberg, materialized_view]`: Iceberg from spike S5; a materialized view's metadata total was still the old one 60 s after an insert in the table-kinds tests), in which case use `get_row_count_changes`. It never fails the run. The volume test emits this value as the metric `row_count`, distinct from `rows_added`.

```sql
{{ icecore.get_row_count_metadata(relation) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | Relation | required | The relation to read, as for `get_last_change_time`. |

## Anomaly engines

Scoring a metric against its history.

### `cortex__score`

Cortex engine: scores one series with the SNOWFLAKE.ML.ANOMALY_DETECTION model trained per table on all its series (models are refreshed by a task, never trained at test time). One CALL of the persisted owner's-rights procedure `ICECORE_SCORE` (created by setup), inside a Snowflake Scripting block that catches every error, so a missing model, privilege or region never fails a test. Snowflake documents that models cannot be shared across roles, so they are trained and scored in setup's role and the dbt role needs only USAGE on `ICECORE_SCORE`. The procedure's arguments are values (the series key, an array of points, numbers, the model name `ICECORE_M_<16 hex>`), never SQL text, and takes no database or schema: it reads the models of its own obs schema only. prediction_interval is 0.99 (low), 0.95 (medium) or 0.90 (high); Cortex high is wider than native high (k = 2 MADs, about 82%), so the same sensitivity flags somewhat fewer points with Cortex. Returns `engine` (cortex), `expected` (forecast), `lower`, `upper`, `score` (signed distance outside the band, 0 inside, as native) and `is_anomaly` (Cortex verdict, and the min_consecutive - 1 latest history points outside the band on the same side). When the model cannot score it returns `error` (message), `state` (`missing`, `stale` or `error`) and `age_days`, and logs a warning. At parse time it returns null. Called by `score`, which falls back to native when it returns `error`.

```sql
{{ icecore.cortex__score(model, series, series_sql, current_value, sensitivity, min_consecutive) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | string | required | The per-table model, `ICECORE_M_` and 16 upper-case hex digits (`cortex_model_name`); anything else is refused. |
| `series` | string | required | The series key (see `cortex_series_key`); the model was trained on its MD5, which the procedure computes. |
| `series_sql` | string | required | As for `score`; only the latest min_consecutive - 1 points are read. |
| `current_value` | number | required | As for `score`. |
| `sensitivity` | string | required | `low`, `medium` or `high` (already resolved). |
| `min_consecutive` | integer | required | As for `score` (already resolved). |
| `observed_at` | string | `none` | As for `score`. |
| `schema` | string | `none` | The obs schema whose `ICECORE_SCORE` scores. The model is always a bare `ICECORE_M_<hex>` name in that same schema; no other schema is ever read. Default the `obs_schema` var. |
| `refresh_days` | number | `none` | A model older than this many days is `stale`. Default `var('icecore').cortex_refresh_days`, 7. |

### `emit_shadow`

Writes a shadow result as one span event `icecore.shadow` through `icecore.emit` (no emitter of its own): `dbt.metric.name`, `dbt.metric.value`, `dbt.node.relation`, optional column and dimension, and `dbt.anomaly.shadow.*` (engine, expected, lower, upper, score, is_anomaly, fallback_reason, primary_engine and agrees, whether both verdicts match). Writes nothing and returns none when `shadow` is none, so a disabled shadow emits no shadow field. Never changes the primary record.

```sql
{{ icecore.emit_shadow(relation, metric, value, shadow) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | string | required | A dbt relation, or an already quoted name. |
| `metric` | string | required | Metric name, as given to `emit_metric`. |
| `value` | number | required | The observed value. |
| `shadow` | dict | required | What `shadow_score` returned. |
| `primary` | dict | `none` | The dispatcher's result, for the comparison keys. |
| `column` | string | `none` | Column name, when the metric is per column. |
| `dimension` | string | `none` | Dimension value, when the metric is per dimension. |

### `get_metric_history`

Lag-aware history reader for the anomaly engines. Reads the points of ONE metric series, the key (relation, metric, column, dimension), from the `METRICS` view of the obs schema and returns a dict `{sql, points, min_points, insufficient_history, lookback_days, delay_minutes}`. `sql` is a SELECT of `observed_at` and `value` ordered by time, which is what `icecore.score` takes as `series_sql`. The window is `observed_at` after now minus `lookback_days` (default 30) and not after now minus the history delay (var `icecore.history_delay_minutes`, default 1, from spike S3 where lag p95 was about 8 s and the worst 20 s), on Snowflake's UTC clock. OBSERVED_TIMESTAMP is NULL on icecore records (S3), so the delay applies to the `observed_at` of the view, the event TIMESTAMP. With fewer than `min_points` (default 7) points `insufficient_history` is true and a line is logged. `min_points` sets only this flag: `score` (native engine) has its own fixed 7 points and never flags a series below them. If reading the history fails (missing view, no grant) a warning is logged, `points` is null and `insufficient_history` is true; the macro never raises. A null column or dimension matches only metrics written without one. At parse time nothing runs and `points` and `insufficient_history` are null.

```sql
{{ icecore.get_metric_history(relation, metric) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | string | required | The relation the metric is on, a dbt Relation or the quoted name `"DB"."SCHEMA"."NAME"` that `emit_metric` writes. |
| `metric` | string | required | The metric name, for example row_count. |
| `column` | string | `none` | The column of the metric. Default none, which matches metrics without a column. |
| `dimension` | string | `none` | The dimension of the metric. Default none, which matches metrics without a dimension. |
| `lookback_days` | integer | `30` | Days of history, at least 1. Default 30. |
| `delay_minutes` | integer | `none` | Minutes to leave out at the recent end, at least 0. Default the `history_delay_minutes` var (1). |
| `min_points` | integer | `7` | Points below which the history is reported as insufficient. Default 7. The native engine's own minimum of 7 is not changed by it. |
| `source` | string | `none` | The relation to read. Default the `METRICS` view of the obs schema. |
| `schema` | string | `none` | The obs schema holding the view. Default the `obs_schema` var. |

### `get_metric_history_sql`

The series SELECT of `get_metric_history` alone, without running a query. `delay_minutes` is used as given. Raises a compiler error for a `lookback_days` below 1 or a negative `delay_minutes`.

```sql
{{ icecore.get_metric_history_sql(relation, metric) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `relation` | string | required | As for `get_metric_history`. |
| `metric` | string | required | As for `get_metric_history`. |
| `column` | string | `none` | As for `get_metric_history`. |
| `dimension` | string | `none` | As for `get_metric_history`. |
| `lookback_days` | integer | `30` | Days of history. Default 30. |
| `delay_minutes` | integer | `1` | Minutes left out at the recent end. Default 1. |
| `source` | string | `none` | The relation to read. Default the `METRICS` view of the obs schema. |
| `schema` | string | `none` | The obs schema holding the view. Default the `obs_schema` var. |

### `native__score`

Native engine: pure SQL, always available, one query over the history. Baseline: seasonal when the series spans more than four weeks and the current observation's (day-of-week, hour-of-day) bucket holds at least 4 points, the band then being computed within that bucket; flat (whole series) otherwise. Band: median ± k × MAD with k = 4 (low), 3 (medium) or 2 (high), raw MAD floored at greatest(`anomaly_mad_floor_pct` × |median|, `anomaly_mad_floor_abs`) (defaults 0.01 and 0), so a constant series of 1000 accepts 970 to 1030 at medium. A median of exactly 0 keeps a zero floor by default: a null rate moving off 0 is always an anomaly (set `anomaly_mad_floor_abs` to tolerate it). Score: signed distance outside the band, 0 inside. is_anomaly: score <> 0 and the min_consecutive - 1 latest history points were also outside their own band on the same side. Fewer than 7 history points never flags (baseline `insufficient_history`). Timestamps are UTC, as in the event table; observed_at defaults to the current UTC time. Known limits: a 4-point bucket minimum means 5 weeks of hourly data are enough for seasonality but bands from small buckets are coarse; a steadily growing metric (a table that only gains rows) drifts out of a median band and is flagged, mostly at high sensitivity, since trend handling (EWMA) is not implemented yet. Called by `score`; returns the contract fields plus `baseline` (flat, seasonal, insufficient_history), `history_points` and `history_days`.

```sql
{{ icecore.native__score(series_sql, current_value, sensitivity, min_consecutive) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `series_sql` | string | required | As for `score`. |
| `current_value` | number | required | As for `score`. |
| `sensitivity` | string | required | `low`, `medium` or `high` (already resolved by `score`). |
| `min_consecutive` | integer | required | As for `score` (already resolved). |
| `observed_at` | string | `none` | As for `score`. |
| `min_points` | integer | `7` | As for `score`. Default 7. |

### `native__score_sql`

The SQL that `native__score` runs, returned as text and not executed, so it can be read or tested without a warehouse. Same baseline, band and verdict rules as `native__score`: one row with expected, lower, upper, score, is_anomaly, baseline, history_points and history_days. Refuses `min_points` below 1 and any number that is not a plain number, so no value is pasted into the SQL as text.

```sql
{{ icecore.native__score_sql(series_sql, current_value, sensitivity, min_consecutive) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `series_sql` | string | required | As for `score`. |
| `current_value` | number | required | As for `score`. |
| `sensitivity` | string | required | `low`, `medium` or `high`. |
| `min_consecutive` | integer | required | As for `score`. |
| `observed_at` | string | `none` | As for `score`. Default none, the current UTC time. |
| `min_points` | integer | `7` | As for `score`. Default 7. At least 1. |

### `score`

Anomaly engine dispatcher (spec section 6). Scores the current value of one metric series and returns a dict with the same fields whatever engine scored: `engine` (native or cortex), `expected`, `lower`, `upper` (acceptance band), `score` (signed distance outside the band, 0 inside), `is_anomaly` (boolean, after the min_consecutive rule) and `fallback_reason` (null, or why the requested engine did not score: `cortex_unavailable`, `history_too_short`, `model_missing`, `model_stale`, `cortex_error`), plus `fallback_detail` (the message behind it) when native scored for a model reason. Selection: `native` never calls Cortex. `cortex` and `auto` trust the `anomaly_engine` var (setup prints `auto` only where Cortex is available) and detect nothing up front. In order, cheapest first, native scores with a recorded `fallback_reason` when: the caller's `capabilities` say Cortex is off (`cortex_unavailable`, no query); no `model` is given or it does not exist (`model_missing`, no query for the first); the series covers fewer than 14 days (`history_too_short`, 1 query); the model is older than `var('icecore').cortex_refresh_days`, a number, default 7, the refresh task's period (`model_stale`, read inside ICECORE_SCORE); or a guarded Cortex call fails, in which case `detect_capabilities` runs once to tell `cortex_unavailable` from `cortex_error`. A working Cortex call costs 2 queries (history, one call of ICECORE_SCORE, which reads the model's age and detects). A fallback is logged and recorded in `fallback_reason`. Computes and returns values only; emitting them is `emit_metric`'s job. At parse time (execute false) settings are validated and the same keys are returned with null values, so a model or test calling score always parses.

```sql
{{ icecore.score(series_sql, current_value) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `series_sql` | string | required | A SELECT returning the history as columns `observed_at` (timestamp) and `value` (number). |
| `current_value` | number | required | The value observed in this run. Null scores 0 and is never an anomaly. |
| `engine` | string | `none` | `native`, `cortex` or `auto`. Default: `var('icecore').anomaly_engine` (auto). |
| `sensitivity` | string | `none` | `low`, `medium` or `high`. Default: `var('icecore').anomaly_sensitivity` (medium). |
| `min_consecutive` | integer | `none` | Integer of at least 1: consecutive out-of-band observations needed before is_anomaly is true. Default: `var('icecore').anomaly_min_consecutive` (1). |
| `observed_at` | string | `none` | UTC timestamp of the current observation (`YYYY-MM-DD HH:MI:SS`), used by seasonal baselines. Default: the current UTC time. |
| `model` | string | `none` | The table's Cortex model as a (qualified) identifier. Default none, which means `model_missing` when Cortex was to score. |
| `series` | string | `none` | The series key the model was trained with. Required with `model`. |
| `capabilities` | dict | `none` | A `detect_capabilities` result the caller already holds. When it says Cortex is off, native scores at once without a query. Default none. |
| `min_points` | integer | `7` | Fewer history points than this never flag (native, baseline insufficient_history). Default 7, the previous fixed value; a monitor passes the history reader's min_points. The Cortex path ignores it: Cortex has its own minimum (12 points per series) and the 14-day history rule applies instead. |
| `schema` | string | `none` | The obs schema whose `ICECORE_SCORE` scores with Cortex. Default the `obs_schema` var. |

### `shadow_score`

Shadow scoring: scores the series with the engine named by `var('icecore').shadow_engine` (none, native or cortex; default none) next to the primary one and returns its fields `engine`, `expected`, `lower`, `upper`, `score`, `is_anomaly` and `fallback_reason`. The primary result is only read, never changed: is_anomaly always comes from the configured engine. Returns none, running no query, when shadow is none or when the shadow engine is the engine that scored. Returns none with one logged warning when Cortex is not available: with `capabilities` saying so no query runs; without it, a call that has a model, a series and 14 days of history runs the history count and the guarded `ICECORE_SCORE` call (which fails), then `detect_capabilities` once to confirm. A call with no model or series, or too little history, stops earlier and returns the cortex row with its reason. A Cortex shadow that cannot score (no or stale model, history under 14 days, error) returns engine cortex with null bands and the reason, never a native result. At parse time it returns none.

```sql
{{ icecore.shadow_score(series_sql, current_value, primary) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `series_sql` | string | required | As for `score`. |
| `current_value` | number | required | As for `score`. |
| `primary` | dict | required | The dispatcher's result; only its `engine` and `is_anomaly` are read. |
| `sensitivity` | string | `none` | As for `score`. |
| `min_consecutive` | integer | `none` | As for `score`. |
| `observed_at` | string | `none` | As for `score`. |
| `model` | string | `none` | The table's Cortex model, as for `score`. |
| `series` | string | `none` | The series key the model was trained with, as for `score`. |
| `shadow_engine` | string | `none` | `none`, `native` or `cortex`. Default: `var('icecore').shadow_engine`. |
| `capabilities` | dict | `none` | A `detect_capabilities` result to reuse, so a monitor scoring many series detects once. Default none: nothing is detected up front, and detection runs once only when a guarded Cortex call fails. |
| `min_points` | integer | `7` | Passed to a native shadow, as for `score`. The Cortex path ignores it. Default 7. |
| `schema` | string | `none` | The obs schema whose `ICECORE_SCORE` scores a Cortex shadow. Default the `obs_schema` var. |

## Root cause

Why a test failed.

### `classify_cause`

Says why a node could be the root cause of a downstream test failure. Returns one of `upstream_failure` (the node errored or failed in this run), `schema_change` (a schema diff signal exists for it), `stale_source` (a freshness breach signal exists for it) or `none`. When several apply, the order of precedence is the order above. A skipped node returns `none`: it was skipped because of something further upstream. Runs no query; the result becomes `dbt.root_cause.kind`.

```sql
{{ icecore.classify_cause(node) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `node` | dict \| string | required | A dbt graph node (any mapping with `unique_id`) or the unique_id itself. Required. |
| `run_results` | list \| dict | `[]` | This run's results. Either dbt's on-run-end `results` (objects with `.node.unique_id` and `.status`) or mappings `{"unique_id": ..., "status": ...}` / `{"node": {"unique_id": ...}, "status": ...}`, or a prepared `{unique_id: status}` mapping (what `walk_upstream` passes, built once per walk). Statuses `error`, `fail` and `runtime error` count as a failure. Default `[]`. |
| `signals` | dict | `{}` | Signals keyed by unique_id: `{"freshness_breaches": {unique_id: detail}, "schema_diffs": {unique_id: [changes]}}`. A node is flagged when its entry is truthy. Either key may be missing. Default `{}`. |

### `emit_root_cause`

End-of-run emitter `root_cause` (var `icecore.root_cause`, default false), called by `end_run` as `emit_root_cause(results, batch)`. For each failed test of this run it calls `walk_upstream` with signals read from the obs-schema views and writes `dbt.root_cause.unique_id`, `dbt.root_cause.kind` and `dbt.root_cause.distance` on that test's node_result LOG (or on a LOG of its own, `icecore.kind = root_cause`, when node_results is off). Signals: `SCHEMA_CHANGES` rows detected in the lookback (var `icecore.root_cause_lookback_hours`, default 24) and nodes whose latest `freshness_lag_seconds` in `METRICS` is an anomaly (nothing scores freshness yet) or whose `icecore.freshness` test failed or warned in this run; upstream failures come from this run's results. The graph is the invocation's own dbt graph, not `LINEAGE_LATEST`, which at on-run-end still holds the previous invocation's edges. Records written by this run are not yet in the views (ingestion lag), so a signal must have been recorded before it. A view that cannot be read logs one warning and that signal stays empty; the run never fails. Nothing is queried when no test failed.

```sql
{{ icecore.emit_root_cause(results, batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | required | The on-run-end results (dbt result objects or the run_results.json shape). |
| `batch` | dict | required | The end_run batch: its items are annotated or appended to. |
| `signals` | dict | `none` | Optional, default none: signals to use instead of reading the views, as for `walk_upstream`. For tests. |

### `walk_upstream`

Finds the root cause of a failed test or node: walks the dbt graph breadth-first over `depends_on`, starting from the parents of `unique_id`, and returns the nearest ancestor that `classify_cause` flags as `{"unique_id": ..., "kind": ..., "distance": n}` (`distance` 1 = direct parent). The nearest cause wins over its kind; among causes at the same distance the kind precedence of `classify_cause` decides (upstream_failure, schema_change, stale_source), then `depends_on` order. Each node is visited once (diamonds), and the run-results map is built once per walk. Walks through skipped and healthy nodes, reaches seeds, snapshots and sources. Returns none when nothing upstream is flagged, when `unique_id` is not in the graph, or at parse time. Runs no query; the result fills `dbt.root_cause.unique_id` and `dbt.root_cause.kind`.

```sql
{{ icecore.walk_upstream(unique_id) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `unique_id` | string | required | unique_id of the failed test (or node) to explain. Required. |
| `results` | list \| dict | `[]` | This run's results, in any shape `classify_cause` accepts as `run_results` (list or prepared map). Default `[]`. |
| `signals` | dict | `{}` | Signals keyed by unique_id, as for `classify_cause`: `{"freshness_breaches": {unique_id: detail}, "schema_diffs": {unique_id: [changes]}}`. Default `{}`. |

## Cost

Attaching warehouse cost to a node.

### `attach_cost`

The end-of-run emitter `cost` of end_run (var `icecore.cost`, default false; one entry in `_icecore_end_run_emitters()`), called as `attach_cost(results, batch)`. It joins the run's `snow.query.id` values (the ones the query events carry) to `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` and, for each node whose queries are all attributed, adds to the batch a LOG `icecore.kind = cost` with the node keys, `dbt.cost.credits` (compute plus query acceleration credits, summed over the node's queries) and `dbt.cost.warehouse` (warehouse names, sorted, comma separated), and sends one METRIC `credits` per node (`icecore.metric` span events keyed by `dbt.node.relation`, or the unique id for a node without relation, with `dbt.node.unique_id`; built by the builder of `emit_metric`, all in one guarded CALL of ICECORE_EMIT). QUERY_ATTRIBUTION_HISTORY lags by hours, so at on-run-end the usual outcome is not available yet: the run logs one `icecore: warning: cost: ...` line and writes nothing for those nodes, and `backfill_cost` fills them on a later run. The attribution query runs inside a Snowflake Scripting block that catches every error, so a missing privilege (grant `SNOWFLAKE.USAGE_VIEWER` to the role, or IMPORTED PRIVILEGES on the SNOWFLAKE database) or an empty result is a warning, never a failed run. With var `cost` false nothing is read or written. Settling: Snowflake never writes attribution rows for many statements (views, plain table creation, about half of short SELECTs, measured in the sandbox), so after var `cost_settle_hours` (default 24) a node whose queries have no attribution row is closed at the credits that were attributed (0 when none) with `dbt.cost.settled = no_attribution`, and gets no metric. A node always gets its cost record once its queries are attributed or settled. Cost regressions: before a node's `credits` point is written it is scored with `icecore.score` against the node's earlier `credits` points (the metrics view of the obs schema, with `get_metric_history`'s window and delay); the metric then carries the `dbt.anomaly.*` keys and, when `is_anomaly`, an `icecore.anomaly` warn event follows it (the record alerts_feed reads). One query counts the history points of all the run's nodes and only nodes with at least 7 points are scored; at most var `cost_score_max_nodes` (default 50) nodes are scored per run, newest first, the backfill sharing that budget. A history that cannot be read scores nothing (a warning); var `cost_score` (default true) turns scoring off. A no-op at parse time. Var `cost_lookback_days` (default 7) bounds the attribution scan.

```sql
{{ icecore.attach_cost(results, batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `results` | list | required | The node results of the run, as end_run passes them (hook operations already left out). |
| `batch` | dict | required | end_run's batch; the cost logs are appended to its items. |

### `backfill_cost`

Fills the costs that were not available at the end of earlier runs. Called by `attach_cost` at the end of a run (so behind var `cost`), as `backfill_cost(batch)`. It reads `icecore.base_events()` for nodes of earlier invocations (the last `cost_lookback_days`, default 7; at most `cost_backfill_limit` nodes per run, default 500) that have `snow.query.id` values (node results and `icecore.query` events) and no row with `dbt.cost.credits`, reads their attribution in QUERY_ATTRIBUTION_HISTORY, and, for each node whose queries are all attributed now, adds to the end_run batch (sent with ICECORE_EMIT_RUN) a LOG `icecore.kind = cost` with `dbt.cost.credits` and `dbt.cost.warehouse`, and a span `icecore.cost_backfill` carrying the METRIC `credits` as an `icecore.metric` span event. Both carry explicit `dbt.invocation_id` of the ORIGINAL invocation (overriding the current run's), `dbt.node.unique_id`, `icecore.backfill = true` and `icecore.backfilled_by` (the invocation that backfilled it). A view joins a cost back to its node on (`invocation_id`, `node_unique_id`), never on `command` or `target`, which describe the run that backfilled it; `node_executions.credits` is the latest cost record's `cost_credits` over that pair. Nodes that already have a cost record (from `attach_cost` or an earlier backfill) are skipped, so nothing is duplicated; records younger than the ingestion lag (about 20 seconds) are not seen yet. Guarded: no event table, an unreadable event table or attribution view, or nothing pending is a warning or silence, never a failed run.

```sql
{{ icecore.backfill_cost(batch) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `batch` | dict | required | end_run's batch; the backfilled items are appended to it, timed with `batch.timing.completed_ns`. |

## Views

The SQL behind the [views](views.md).

### `alerts_feed_view_sql`

The SELECT of the `alerts_feed` view, one row per incident, deduplicated for the Snowflake ALERT objects and the app. It unions failed tests (status fail, error or warn, from the test_results SQL), anomalies (metrics rows with anomaly_is_anomaly true) and schema changes, plus two empty slots (silence_events, filled by story uc6-silence, and cost_regressions, filled later) that select zero rows with the signal columns. Columns `incident_key` (md5 of node_key, kind and window_start), `kind` (test_failure, anomaly, schema_change, silence or cost), `node_key`, `window_start`, `severity`, `title`, `first_detected_at`, `last_detected_at`, `occurrences`, `last_invocation_id`, `root_cause_unique_id` and `root_cause_kind` (test failures with a root-cause attribution, else null), `acknowledged_at` and `acknowledged_by` (the earliest `icecore.ack` event of the key written by ICECORE_ACK at or after the incident's first detection, null when none). The window is var icecore.alert_window_hours, default 24 hours, so the same failure on two runs inside one window is one key and the next window alerts again.

```sql
{{ icecore.alerts_feed_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `base_events`

The filtered, typed SELECT every icecore view starts from, returned as SQL text to use as a CTE body: `with events as (<the rendered icecore.base_events()>)`, rendered inside a macro or model. It reads the event table that `resolve_event_table` returns for the database and keeps only the records of icecore's two procedures in this environment (spikes/GATE1.md, "Identity and record shape"). The predicates come first and in this order, so the filter sits directly on the table scan: `RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG')`; `RESOURCE_ATTRIBUTES` `snow.database.name` = the database, `snow.schema.name` = the icecore schema and `snow.executable.name like 'ICECORE_EMIT%'`; then the icecore identity, `SCOPE:name = 'icecore'` (ICECORE_EMIT_RUN records) or a SPAN_EVENT whose name starts with `icecore.` (ICECORE_EMIT records, SCOPE `global`). Records of other libraries, other procedures, other schemas or other databases sharing the table are never returned; native dbt Projects records are not either, until their own predicate is defined (GATE1.md, open item 8). Columns: `event_at` (TIMESTAMP), `started_at` (START_TIMESTAMP), `observed_at` (OBSERVED_TIMESTAMP; NULL on every ICECORE_EMIT span event, spike S3), `record_type`, `record_name`, `severity`, `scope_name`, `trace_id`, `span_id`, `parent_span_id`, `executable_name`, `emit_query_id` (the emitting CALL's query id), one typed column per attribute of spec section 4 (strings; `result_rows_affected`, `test_failures`, `icecore_schema_version` as NUMBER; `result_execution_time_s`, `metric_value`, `anomaly_expected`, `anomaly_lower`, `anomaly_upper`, `anomaly_score`, `cost_credits` as FLOAT; `anomaly_is_anomaly` as BOOLEAN), `value` and `attributes` (raw RECORD_ATTRIBUTES). Typed columns use TRY_TO_*, so a malformed value reads as null and never fails a view. Spans are written after the fact with dbt's times, so do not window incremental reads on `event_at` alone.

```sql
{{ icecore.base_events() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none: resolved with `resolve_event_table(database)` when the macro executes (at parse time a placeholder is rendered). |
| `database` | string | `none` | The database whose icecore procedures wrote the records. Default `target.database`. A plain identifier is matched in upper case, as Snowflake stores it; any other name is matched as written. |
| `obs_schema` | string | `none` | The schema holding ICECORE_EMIT and ICECORE_EMIT_RUN. Default `var('icecore', {}).get('obs_schema', 'ICECORE_OBS')`. Matched like `database`. |

### `capabilities_view_sql`

The SELECT of the `capabilities` view, the latest detection per database. It reads `icecore.base_events()` and keeps the `icecore.capabilities` span events with their `icecore.capability.*` keys. Columns: `database_name`, `detected_at` (the event TIMESTAMP), `edition`, `edition_source`, `event_table`, `event_table_level`, `cortex_available`, `cortex_reason`, `log_level`, `trace_level`, `icecore_version` and `invocation_id`.

```sql
{{ icecore.capabilities_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `create_views`

Create the icecore views in `<target.database>.<schema>`, the step `create_views` of `setup`. One list, `_icecore_views`, holds every view (`METRICS`, `SCHEMA_CHANGES`, `TEST_RESULTS`, `LINEAGE`, `LINEAGE_LATEST`, `CAPABILITIES`, `INVOCATIONS`, `NODE_EXECUTIONS`, `SILENCE_EVENTS`, `ALERTS_FEED`); a later story appends its view there. Each is `CREATE OR REPLACE VIEW ... COPY GRANTS` over `icecore.base_events()`, so a second run keeps the grants made on the views. The event table is resolved once. No tables are created. The schema must exist and the role needs CREATE VIEW on it. Returns the qualified view names (empty at parse time).

```sql
{{ icecore.create_views() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema to create them in, which is also the schema the base filter reads. Default the `obs_schema` var. |

### `event_table_to_read`

Maps an EVENT_TABLE parameter value to the relation to read: `SNOWFLAKE.TELEMETRY.EVENTS` (any case) becomes `SNOWFLAKE.TELEMETRY.EVENTS_VIEW`, any other table is returned as it is, none or empty gives none. No query.

```sql
{{ icecore.event_table_to_read(event_table) }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | required | The value of the EVENT_TABLE parameter. |

### `invocations_view_sql`

The SELECT of the `invocations` view, one row per dbt invocation. It reads `icecore.base_events()` and joins on `dbt.invocation_id` the `icecore.run_start` span event (ICECORE_EMIT), the `icecore.invocation` span and the run-end LOG, `icecore.kind = run_end` (both ICECORE_EMIT_RUN). Columns: `invocation_id`, `runtime`, `command`, `target`, `project`, `dbt_version`, `started_at`, `ended_at`, `duration_s`, `status`, `nodes_total`, `nodes_success`, `nodes_failed`, `nodes_warned`, `nodes_skipped`, `credits`. `status` is the run-end LOG's `dbt.invocation.status` (success, warn or error). A run with a start and no end shows `running` when its start record landed less than 24 hours ago, else `unfinished`, with `ended_at` and the counts null. `credits` is the sum of the nodes' credits (latest cost record per node, backfilled ones included), null until a cost record exists. Clocks: `started_at`, `ended_at` and `duration_s` are dbt's clock, the dbt host's, read from the `dbt.invocation.*` attributes; the TIMESTAMP of the records is Snowflake's clock, the time of the CALL. The two are never subtracted from each other.

```sql
{{ icecore.invocations_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `lineage_latest_view_sql`

The SELECT of the `lineage_latest` view: the same columns as `lineage`, only the edges of each project's latest invocation that emitted lineage.

```sql
{{ icecore.lineage_latest_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `lineage_view_sql`

The SELECT of the `lineage` view, one row per edge per invocation. It reads `icecore.base_events()`, keeps the LOG records `icecore.kind = lineage` and flattens their `dbt.lineage.edges` lists (a run's edges may span several records). Columns: `invocation_id`, `project`, `parent_unique_id`, `child_unique_id`, `recorded_at`.

```sql
{{ icecore.lineage_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `metrics_view_sql`

The SELECT of the `metrics` view, one row per metric a monitor computed. It reads `icecore.base_events()` and keeps the `icecore.metric` span events that `icecore.emit_metric` writes. Columns: `relation` (dbt.node.relation, the quoted DB.SCHEMA.NAME), `"COLUMN"` (dbt.metric.column; COLUMN is a reserved word, so quote it), `dimension`, `metric`, `observed_at` (the event TIMESTAMP, since OBSERVED_TIMESTAMP is NULL on icecore records, spike S3), `value` (FLOAT), the anomaly fields `anomaly_engine`, `anomaly_expected`, `anomaly_lower`, `anomaly_upper`, `anomaly_score`, `anomaly_is_anomaly` (null while the metric was not scored, the view still succeeds), then `invocation_id`, `node_unique_id` and `emit_query_id`. The `icecore.anomaly` warn events repeat their metric event's keys, so they are not rows of this view.

```sql
{{ icecore.metrics_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `node_executions_view_sql`

The SELECT of the `node_executions` view, one row per node per invocation. It reads `icecore.base_events()` and joins on `dbt.invocation_id` and `dbt.node.unique_id` the `icecore.node` span, the node-result LOG (`icecore.kind = node_result`) and the `icecore.query` span events. Columns: `invocation_id`, `unique_id`, `type`, `name`, `materialization`, `relation`, `status`, `message` (dbt.result.message, as capped), `rows_affected`, `execution_time_s`, `started_at`, `completed_at`, `query_id` (the result's `snow.query.id`, else the first query event's), `query_ids` (array of every query id of the node), `credits`, `credits_settled`, `root_cause_unique_id`, `recorded_at` (the TIMESTAMP of the node's result record, Snowflake's clock, used by the silence task to compare with Snowflake's now; null for a node with a span and no result). `credits` is the latest cost record's `dbt.cost.credits` for the node on its invocation (attach_cost's LOG, or a backfilled one keyed to the original invocation); `credits_settled` is its `dbt.cost.settled` (`no_attribution` when settled with 0 credits, else null). Both are null until a cost record exists. `root_cause_unique_id` is read from the node-result LOG and is null until UC4 writes it. A skipped node has a result and no span, so its times are null. Clocks: `started_at` and `completed_at` are dbt's clock, the dbt host's, read from the `dbt.node.*` attributes; the TIMESTAMP of the records is Snowflake's clock, the time of the CALL. The two are never subtracted from each other.

```sql
{{ icecore.node_executions_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `resolve_event_table`

The event table the icecore views read for a database, resolved with `SHOW PARAMETERS LIKE 'EVENT_TABLE' IN DATABASE <database>` (spikes/GATE1.md, "What setup creates"): the table it names, or `SNOWFLAKE.TELEMETRY.EVENTS_VIEW` when it names the account table `SNOWFLAKE.TELEMETRY.EVENTS` (readers then need the `EVENTS_VIEWER` application role). Raises a compiler error when no event table is active, since no view could read anything. Returns none at parse time.

```sql
{{ icecore.resolve_event_table() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `database` | string | `none` | The database to resolve for. Default `target.database`. |

### `schema_changes_view_sql`

The SELECT of the `schema_changes` view, one row per detected column change. It reads `icecore.base_events()` and keeps the span events named `icecore.schema_change`. Columns: `relation`, `change_kind` (added, removed or type_changed), `"COLUMN"` (quote it, COLUMN is a reserved word), `from_type`, `to_type`, `detected_at` (the event TIMESTAMP), `invocation_id`, `node_unique_id`, `emit_query_id`. Nothing emits these records yet; the keys are the spec's, `dbt.schema.change_kind`, `dbt.schema.column`, `dbt.schema.from_type` and `dbt.schema.to_type` next to `dbt.node.relation`, and the story that emits them must write exactly these. A missing key reads as null.

```sql
{{ icecore.schema_changes_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `schema_columns_view_sql`

The SELECT of the `schema_columns` view, one row per column per schema_changes run (the full column state, not the changes). It reads `icecore.base_events()` and the span events named `icecore.schema_signature`, which the schema_changes test writes on every run, one per chunk of columns under the payload cap. Columns: `relation`, `"COLUMN"` (quote it), `data_type`, `snapshot_at` (the same for every column of one run), `is_new` (the relation had a previous snapshot and the column is not in it; false on the first snapshot, as in Elementary). Only complete snapshots (every chunk landed) are listed and `invocation_id`.

```sql
{{ icecore.schema_columns_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `silence_events_view_sql`

The SELECT of the `silence_events` view, one row per missed run. It reads `icecore.base_events()` and keeps the `icecore.silence` span events that the silence task writes through `ICECORE_EMIT`. Columns: `unique_id`, `expected_at`, `grace` (minutes), `expected_every_minutes`, `last_success_at`, `detected_at`, `channel`, `severity`. `expected_at` and `last_success_at` are keys of the verdict, UTC to the second, computed on Snowflake's clock from the TIMESTAMP of the node-result records; `detected_at` is the TIMESTAMP of the verdict record, Snowflake's clock too.

```sql
{{ icecore.silence_events_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

### `test_results_view_sql`

The SELECT of the `test_results` view, one row per test per invocation. It reads `icecore.base_events()` and keeps the node-result LOGs (`icecore.kind = node_result`) of test nodes (unique id starting `test.`); a duplicated record collapses to the latest. Columns: `invocation_id`, `project`, `test_unique_id`, `test_name`, `test_type` (generic or singular), `"COLUMN"` (quote it), `severity`, `status`, `failures`, `message` (redacted by default), `execution_time_s`, `root_cause_unique_id` and `root_cause_kind` (null unless root-cause attribution wrote them), the scored-test fields `engine`, `expected`, `lower`, `upper`, `score` (null unless the test wrote dbt.anomaly.* keys), `executed_at` (the event TIMESTAMP) and `query_id`. The spec's `model_unique_id` is not a column: no emitter writes it yet.

```sql
{{ icecore.test_results_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `event_table` | string | `none` | The relation to read. Default none, resolved with `resolve_event_table` when the macro executes. |
| `database` | string | `none` | The database whose procedures wrote the records. Default `target.database`. |
| `obs_schema` | string | `none` | The schema holding the emitter procedures. Default the `obs_schema` var. |

## Elementary-compatible views

Created when `setup` runs with `compat: elementary`.

### `create_compat_views`

Creates the Elementary-compatible views in the compat schema (setup step create_compat_views, run when setup gets compat elementary). The compat schema is var icecore.compat_schema, default the obs schema name followed by _ELEMENTARY; it is created if missing. Every view is CREATE OR REPLACE ... COPY GRANTS over a core view of the obs schema (TEST_RESULTS, METRICS, SCHEMA_CHANGES, LINEAGE_LATEST), so grants made on them survive a second run and the core views must exist first. The views come from one list, _icecore_compat_views(), that later UC8 stories append to. They match the relation names, column names, column order and types of the Elementary package pinned in macros/compat/ELEMENTARY_VERSION; a column icecore cannot fill is a typed NULL. Creates no table. Returns the qualified view names, none at parse time.

```sql
{{ icecore.create_compat_views() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `schema` | string | `none` | The obs schema the core views are in. Default the obs_schema var. |
| `compat` | string | `'elementary'` | What the views imitate: only elementary. Any other value is refused. |
| `compat_schema` | string | `none` | The schema to create the views in. Default var icecore.compat_schema, else the obs schema name followed by _ELEMENTARY. Only letters, digits and underscores are accepted; anything else is a compile error. |

### `dbt_invocations_view_sql`

The SELECT of the compat view dbt_invocations: Elementary's 35 columns, in its order and types (Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION), over the core view INVOCATIONS only, so it has the core view's rows. Filled: invocation_id, command, dbt_version, target_name, run_started_at, run_completed_at, generated_at and created_at from the invocation's times (dbt's clock), project_name from the run's dbt project, and elementary_version, the pinned release, because edr checks its own version against it. Every other column is a typed NULL: job_*, full_refresh, invocation_vars, vars, target_database, target_schema, target_profile_name, threads, selected, yaml_selector, project_id, env, env_id, cause_category, cause, pull_request_id, git_sha, orchestrator, dbt_user, job_url, job_run_url, account_id, target_adapter_specific_fields. Text timestamps are in Elementary's format YYYY-MM-DD HH24:MI:SS, which Snowflake can cast.

```sql
{{ icecore.dbt_invocations_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema holding the core views. Default the current one. |

### `dbt_models_view_sql`

The SELECT of the compat view dbt_models: Elementary's 23 Snowflake columns, in its order and types (Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION), over the core views NODE_EXECUTIONS and LINEAGE_LATEST only. One row per model that ran, from its latest execution. Filled: unique_id, name, materialization, alias, database_name and schema_name (read from the node's relation), package_name (from the unique_id), generated_at and depends_on_nodes (a JSON list of the node's parents in the latest lineage). Every other column is a typed NULL, because icecore holds no manifest metadata: description, tags, meta, owner, paths, checksum, metadata_hash, unique_key, incremental_strategy, group_name, access, depends_on_macros. A model that has never run is absent.

```sql
{{ icecore.dbt_models_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema holding the core views. Default the current one. |

### `dbt_run_results_view_sql`

The SELECT of the compat view dbt_run_results: Elementary's 23 columns, in its order and types, over the core view NODE_EXECUTIONS only, so it has one row per node per invocation and the core view's row count. Filled: model_execution_id (invocation_id and unique_id joined by a dot, as Elementary builds it), unique_id, invocation_id, name, status, resource_type, execution_time, rows_affected, query_id, materialization, execute_started_at and execute_completed_at (dbt's clock, with microseconds), generated_at and created_at (recorded_at, Snowflake's clock). message (the node result's message as dbt.result.message holds it: redacted by default, and capped with its record at payload_cap_kb, so a message over 64 KB is the truncated text). Typed NULLs: compile_started_at, compile_completed_at, full_refresh, compiled_code, failures, thread_id, adapter_response, group_name. The two clocks are never subtracted from each other.

```sql
{{ icecore.dbt_run_results_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema holding the core views. Default the current one. |

### `dbt_sources_view_sql`

The SELECT of the compat view dbt_sources: Elementary's 22 columns, in its order and types, over the core view LINEAGE_LATEST only. One row per source that is a parent of some node in the latest lineage. Filled: unique_id, source_name, name and package_name (all read from the unique_id source.package.source.table) and generated_at (the latest lineage record). Every other column is a typed NULL: database_name, schema_name, identifier, loaded_at_field, the freshness settings, relation_name, tags, meta, owner, paths, descriptions and metadata_hash.

```sql
{{ icecore.dbt_sources_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema holding the core views. Default the current one. |

### `dbt_tests_view_sql`

The SELECT of the compat view dbt_tests: Elementary's 29 columns, in its order and types, over the core views NODE_EXECUTIONS and TEST_RESULTS only. One row per test that ran, from its latest execution. Filled: unique_id, name, short_name (the test's name), test_column_name, severity, type (generic or singular), package_name and generated_at. Tests are not lineage edges, so depends_on_nodes and parent_model_unique_id are NULL, like every other column icecore has no source for (description, tags, meta, test_params, paths, database_name, schema_name and the rest).

```sql
{{ icecore.dbt_tests_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema holding the core views. Default the current one. |

### `elementary_data_monitoring_metrics_view_sql`

The SELECT of the compat view data_monitoring_metrics: one row per row of the core view METRICS, with the 15 columns, the order and the types of Elementary's table of that name (Elementary dbt package 0.26.1, pinned in macros/compat/ELEMENTARY_VERSION). It selects only from METRICS. Filled: id (md5 of the series key and observed_at), full_table_name (DB.SCHEMA.NAME, upper case, unquoted, as Elementary writes it), column_name, metric_name and metric_type (the metric), metric_value as FLOAT, bucket_start, bucket_end, updated_at and created_at (observed_at), and dimension and dimension_value (icecore's name=value dimension split at the first equals sign). Typed NULLs: source_value, bucket_duration_hours, metric_properties.

```sql
{{ icecore.elementary_data_monitoring_metrics_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema, as obs_schema() renders it. Default the current obs schema. |

### `elementary_schema_columns_snapshot_view_sql`

The SELECT of the compat view schema_columns_snapshot, with the 8 columns, the order and the types of Elementary's table of that name (Elementary dbt package 0.26.1). It selects only from the core view SCHEMA_CHANGES and keeps the changes that leave the column in the table, added and type_changed: a removed change leaves no column state, so it has no row. This view is a change log, not a snapshot: Elementary appends one row per current column on every run, so a "latest snapshot per table" query lists every column there, but here it returns only the columns changed in the latest change, because a column has a row only when it was added or its type changed (a column that never changed has the row of its first appearance, or none if it predates icecore's monitoring). Matching Elementary needs a per-run emission of the full column state, which icecore does not do yet. Filled: column_state_id (md5 of table, column and type), full_column_name, full_table_name (DB.SCHEMA.NAME upper case unquoted), column_name, data_type (to_type), is_new (the change is an addition), detected_at and created_at (detected_at).

```sql
{{ icecore.elementary_schema_columns_snapshot_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema, as obs_schema() renders it. Default the current obs schema. |

### `elementary_test_results_view_sql`

The SELECT of the compat view `elementary_test_results`: one row per test per invocation, with the 28 columns, the order and the types of Elementary's table of that name (Elementary dbt package 0.26.1, pinned in macros/compat/ELEMENTARY_VERSION). It selects only from the core view TEST_RESULTS of the obs schema, so its row count is the core view's. Filled: id and test_execution_id (invocation_id and test_unique_id joined by a dot), test_unique_id, invocation_id, detected_at and created_at (executed_at), column_name, test_type (anomaly_detection when the test was scored, else dbt_test), test_sub_type, test_results_description (message), test_name, test_short_name, severity (dbt's configured value as it is, default ERROR) and status (lower case), failures and failed_row_count, and other (a JSON object of the anomaly result for scored tests). Typed NULLs: data_issue_id, model_unique_id, database_name, schema_name, table_name, owners, tags, test_results_query, test_params, test_alias, result_rows.

```sql
{{ icecore.elementary_test_results_view_sql() }}
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `obs` | string | `none` | The quoted obs schema, as obs_schema() renders it. Default the current obs schema. |
