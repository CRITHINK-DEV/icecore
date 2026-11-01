# Configuration

This page lists every setting icecore reads today: the `icecore` var, the setup and doctor operations, the hooks and the views. It describes what is merged on `develop`. Features that are not merged yet are marked **coming** and have no detail here.

For a first install, follow the [quickstart](quickstart.md).

## The `icecore` var

All settings sit under one var, `icecore`, in your project's `dbt_project.yml`:

```yaml
vars:
  icecore:
    telemetry: true
    result_message: redacted
```

Three rules apply to the var.

- **A value you pass replaces the whole dict.** `--vars '{icecore: {telemetry: false}}'` drops every other key you set in `dbt_project.yml`. The macros then use their own defaults, listed below. If you changed `obs_schema`, include it in the `--vars` too.
- **Every key is optional.** A missing key means the default in the tables below. The defaults are the ones in the package's `dbt_project.yml`; each was checked against the macro that reads it.
- **Values are literals.** dbt does not render Jinja in `vars:` of `dbt_project.yml`. `obs_schema: "{{ env_var('X') }}"` creates a schema with that text as its name.

Booleans accept `true` and `false`. The run hooks also read the strings `true`, `1`, `yes` and `on` (any case) as true.

### Run telemetry

These keys control what the `start_run` and `end_run` hooks write.

| Var | Default | Type | What it does | Privacy or cost effect |
| --- | --- | --- | --- | --- |
| `telemetry` | `true` | boolean | `false` makes both hooks write nothing. | Cost: no rows at all. Use it to switch icecore off for one run with `--vars`. |
| `node_results` | `true` | boolean | `end_run` writes one record per node result: status, rows affected, execution time, message, test failures. | Cost: one row per node per run. Privacy: carries `dbt.result.message`, see `result_message`. |
| `query_events` | `true` | boolean | `end_run` adds one `icecore.query` event with the Snowflake query id to each node's span. | Cost: one row per node query per run. The query id is what cost attribution joins on later. |
| `lineage` | `true` | boolean | `end_run` writes the project's dependency edges (parent and child unique ids, between models, seeds, snapshots and sources) as log records. The whole graph is sent every run, not only the selected nodes. Edges that do not fit under `payload_cap_kb` are split over as many records as needed, and none is dropped. A graph with no edges writes no record. | Cost: at least one row per run, one more for each payload cap of edges. Privacy: unique ids only, no data. |
| `result_message` | `redacted` | string | How much of dbt's result message reaches the event table: `redacted`, `full` or `none`. An unknown value reads as `redacted`. | Privacy, see below. |
| `payload_cap_kb` | `64` | number | Largest size of one record's attributes. A longer string is cut and the record gets `icecore.truncated = true`. Clamped to 1 to 256. | Cost: the high end of the cost estimate. Snowflake silently drops a record of about 1 MB (spike S2). |

#### `result_message`

dbt's message can quote data values from a Snowflake error. People who can read the event table may not be allowed to read your data. The setting has three modes:

- `redacted` replaces every single-quoted literal with `'?'` and every double-quoted one with `"?"`, then cuts the text at 1,000 characters.
- `full` keeps dbt's text as it is.
- `none` leaves the key out.

Checked on the sandbox with a model that casts `'jane@x.com'` to a number:

| Mode | `dbt.result.message` in the event table |
| --- | --- |
| `redacted` | `... failed on column N with error: Numeric value '?' is not recognized` |
| `full` | `... failed on column N with error: Numeric value 'jane@x.com' is not recognized` |
| `none` | key absent |

The same text feeds the `message` column of the `test_results` view. `redacted` only hides quoted literals. An error message can still name tables and columns.

### Where icecore writes

| Var | Default | Type | What it does | Privacy or cost effect |
| --- | --- | --- | --- | --- |
| `obs_schema` | `ICECORE_OBS` | string | Schema of the emitter procedures and the views, in the target database. Upper-cased. | None. It must match the schema `setup` created, or runs write through a different set of procedures. |

If you give `setup` another schema with its `schema` argument, set this var to the same name. `setup` prints a warning when the two differ.

### Monitor and anomaly settings

The tests do not score anomalies yet. These keys are read today by the scoring macros `icecore.score` and `icecore.native__score`, and by the metadata and history readers.

| Var | Default | Type | What it does | Privacy or cost effect |
| --- | --- | --- | --- | --- |
| `anomaly_engine` | `auto` | string | `native`, `cortex` or `auto`. Any other value is a compile error. The Cortex engine is **coming**: today every choice scores with the native engine, and `auto` and `cortex` log why. | Cost: none today. |
| `anomaly_sensitivity` | `medium` | string | `low`, `medium` or `high`. Sets the band width to 4, 3 or 2 median absolute deviations. | None. |
| `anomaly_min_consecutive` | `1` | integer | How many points in a row must be outside the band. At least 1. | None. |
| `anomaly_mad_floor_pct` | `0.01` | number | Smallest band half-width as a share of the expected value. | None. |
| `anomaly_mad_floor_abs` | `0` | number | Smallest band half-width as an absolute value. | None. |
| `shadow_engine` | `none` | string | Recorded on each run's start and end records. Shadow scoring is **coming**. | None. |
| `history_delay_minutes` | `1` | number | Metric history leaves out the newest minutes, because of ingestion lag. Spike S3 measured a p95 lag of about 8 s and a worst case of 20 s. | None. |
| `metadata_fallback_kinds` | `[iceberg]` | list | Table kinds whose metadata row count is not trusted. The metadata row-count reader returns none for them. | The fallback reader over `CHANGES` is opt-in and reads data (spike S5). |
| `dimension_max_values` | `20` | integer | Distinct values counted per dimension column. The rest are grouped as `other`. At least 1. | Cost: more values, more metric rows. |

### Declared, not read yet

These keys are in the package's `dbt_project.yml`, but no merged macro reads them. Setting them changes nothing today.

| Var | Default | Status |
| --- | --- | --- |
| `monitors` | `true` | **coming** |
| `cost` | `false` | **coming** |
| `root_cause` | `false` | **coming** |
| `alerts` | `false` | **coming** |

The `meta.sla` and `meta.alerts` model settings, the alerts and the app are **coming** too.

## Hooks

Add the two hooks to your `dbt_project.yml`. Both render to an empty string, so the hook itself runs no SQL.

```yaml
on-run-start: "{{ icecore.start_run() }}"
on-run-end: "{{ icecore.end_run(results) }}"
```

| Hook | What it writes | When it writes nothing |
| --- | --- | --- |
| `icecore.start_run()` | One `icecore.run_start` span event through `ICECORE_EMIT`, with the run's attributes, `dbt.version`, `icecore.anomaly_engine` and `icecore.shadow_engine`. | `telemetry: false`, or at parse time. |
| `icecore.end_run(results)` | Through `ICECORE_EMIT_RUN`: the invocation span, one span per node, the `node_results`, `query_events` and `lineage` records, and one invocation-end log with the status (`success`, `warn` or `error`) and the node counts. Sent in as many `CALL`s of at most 512 KB as needed. | `telemetry: false`, or at parse time. |

Every record carries `dbt.invocation_id`, which groups a run. A telemetry failure never fails your dbt run: if a procedure or a grant is missing, icecore logs one warning per hook and dbt carries on. If one end-of-run emitter reports an error, `end_run` skips it, warns, and sends the others. One failure cannot be caught: if the query that makes the `CALL` itself cannot run (no warehouse, a closed connection), dbt raises it.

On dbt 1.8, `dbt source freshness` runs no project hooks unless `flags: {source_freshness_run_project_hooks: true}` is set (spike S3).

## Run-operations

Run these with `dbt run-operation`. Arguments go in `--args`.

### `icecore.setup`

```bash
dbt run-operation icecore.setup --args '{schema: ICECORE_OBS, compat: none, app: false, runs_per_day: 24}'
```

`setup` runs these steps in order and prints one line for each: detect capabilities, create the schema, create the two procedures, set the database's log and trace levels, record the capabilities, print the cost estimate, create the views. It creates no tables. Running it again changes nothing, and grants made on the procedures and views are kept.

| Argument | Default | Type | What it does |
| --- | --- | --- | --- |
| `schema` | the `obs_schema` var (`ICECORE_OBS`) | string | Schema to create in the target database. |
| `compat` | `none` | string | Any other value, such as `elementary`, adds a `create_compat_views` step. The compat views are **coming**: the step prints `planned, not available yet` and does nothing. |
| `app` | `false` | boolean | `true` adds a `deploy_app` step. The app is **coming**: the step prints `planned, not available yet` and does nothing. |
| `runs_per_day` | none | number | dbt runs per day, for the monthly part of the cost estimate. Without it the estimate prints rows per run and asks for it. |

Checked on the sandbox with `compat: elementary, app: true`: steps 8 and 11 print `planned, not available yet`. The steps for alerts and tasks always print the same line, because they are **coming**.

The role that runs `setup` needs:

- USAGE on the target database;
- CREATE SCHEMA on it, or an existing schema it can use;
- CREATE PROCEDURE and CREATE VIEW on the schema;
- read access to the event table the views read (the `EVENTS_VIEWER` application role when it is the account table).

`setup` changes the database's `LOG_LEVEL` to at least `INFO` and `TRACE_LEVEL` to `ON_EVENT` or `ALWAYS`, if its role may. When it may not, it prints the `ALTER DATABASE` statement for an admin and carries on. It never runs `ALTER ACCOUNT`.

### `icecore.doctor`

```bash
dbt run-operation icecore.doctor --args '{schema: ICECORE_OBS}'
```

`doctor` never fails the run. It checks the event table, the database's levels, makes one test `CALL` of each procedure, and counts icecore records of the last 24 hours. The test calls are tagged `icecore.doctor.probe` and are not counted.

| Argument | Default | Type | What it does |
| --- | --- | --- | --- |
| `schema` | the `obs_schema` var | string | Schema to check. |

It ends with `OK: <n> icecore records in the last 24 h`, or with `no records in 24 h` and the likely cause.

### `icecore.estimate_telemetry_cost`

```bash
dbt run-operation icecore.estimate_telemetry_cost --args '{runs_per_day: 24, nodes: 100}'
```

It reads the dbt graph and runs no query. It estimates rows per run, rows per month and ingestion credits at Snowflake's published Telemetry Data Ingest rate (0.0212 credits per GB). It follows the `node_results`, `query_events`, `lineage` and `payload_cap_kb` settings: with `node_results` and `query_events` off, the same 3-node project goes from 18 rows to 12 rows per run. Lineage records are counted only when `lineage` is on; there is no argument for them.

| Argument | Default | Type | What it does |
| --- | --- | --- | --- |
| `runs_per_day` | none | number | dbt runs per day that fire the hooks. Needed for the monthly figures. |
| `nodes` | the models, seeds, snapshots and tests of the graph | integer | Nodes per run. |
| `query_events_per_node` | `1` | integer | Queries per node, one query event each. |
| `failing_tests_per_run` | `0` | integer | Failing tests per run that send a sample. Samples are **coming**. |
| `days` | `30` | integer | Days per month. |
| `bytes_per_record` | `1500` | integer | Bytes per row at the low end of the range. The high end is the payload cap. |

Until Telemetry Data Ingest replaces Logging on your account, Snowflake bills Logging instead, which a row count cannot predict. The command says so and names the `METERING_HISTORY` service types to check.

## What `setup` creates

`setup` creates these objects in `<target_db>.<obs_schema>`.

| Object | Kind | What it is |
| --- | --- | --- |
| `ICECORE_EMIT` | SQL procedure | Writes span events. At most 100 events and 512 KB per `CALL`. |
| `ICECORE_EMIT_RUN` | Python procedure | Writes spans with real timings, and log records, at the end of a run. At most 512 KB per `CALL`. |
| `METRICS` | view | One row per metric a monitor computed. |
| `SCHEMA_CHANGES` | view | One row per detected column change. |
| `TEST_RESULTS` | view | One row per test per invocation. |
| `LINEAGE` | view | One row per dependency edge per invocation. |
| `LINEAGE_LATEST` | view | The edges of each project's latest invocation that wrote lineage. |
| `CAPABILITIES` | view | The latest detection of edition, event table, levels and Cortex, per database. |

The procedures run with their owner's rights. The role that runs dbt needs USAGE on the database, the schema and both procedures. Readers need SELECT on the views.

Which view fills from which record today: `TEST_RESULTS` and `LINEAGE` fill from the hooks, `CAPABILITIES` from `setup` and `doctor`, `METRICS` from the `icecore.freshness` and `icecore.volume` tests. `SCHEMA_CHANGES` stays empty until the test that writes it is merged.

## Generic tests

Two generic tests are merged: `icecore.freshness` and `icecore.volume`. Both read Snowflake metadata and scan no data. Each writes one metric through `icecore.emit_metric`, which fills the `METRICS` view, and then compares it with your threshold. The verdict comes only from that comparison. A failed telemetry `CALL` is a logged warning and never changes the result.

```yaml
models:
  - name: orders
    data_tests:
      - icecore.freshness:
          max_lag: 2h
      - icecore.volume:
          min_rows: 1
          max_rows: 1000000
```

Both tests accept dbt's own `config:`, such as `severity: warn`, to warn instead of failing.

### `icecore.freshness`

Reads the last committed change of the relation (`SYSTEM$LAST_CHANGE_COMMIT_TIME`), measures the lag against Snowflake's clock, writes it as the metric `freshness_lag_seconds`, and fails when the lag is above `max_lag`.

| Argument | Default | Type | What it does |
| --- | --- | --- | --- |
| `max_lag` | none | number or string | Largest accepted lag. A number is seconds. A string is a number with a unit `s`, `m`, `h` or `d`: `30m`, `2h`, `1d`, `1.5h`. Spaces and case are ignored. Combined units such as `1h30m` are not accepted. A negative number, a boolean or any other text is a compile error. Without it the test records the metric and always passes. |

### `icecore.volume`

Reads the relation's total row count from `INFORMATION_SCHEMA.TABLES`, writes it as the metric `row_count`, and fails when it is below `min_rows` or above `max_rows`.

| Argument | Default | Type | What it does |
| --- | --- | --- | --- |
| `min_rows` | none | integer | Smallest accepted row count. |
| `max_rows` | none | integer | Largest accepted row count. |

Each must be a non-negative number. `min_rows` above `max_rows` is a compile error. With neither, the test records the metric and always passes.

### When a metric cannot be read

Sometimes the test has nothing to measure:

- `freshness`: the relation was never written, or its last change time is not readable.
- `volume`: the relation is a view, an external table or an Iceberg table, or its row count is missing or not readable. (The `metadata_fallback_kinds` var sets which kinds are not trusted.)

In that case no metric is written, and the test logs a warning. What happens next depends on the threshold:

- **No threshold set** (`max_lag`, or `min_rows` and `max_rows`): the test passes.
- **A threshold set**: the test returns one row with `reason` set to `unmeasured: <why>`, so the test's severity applies. With the default severity the test fails. With `severity: warn` it warns.

A threshold you cannot check is not reported as a pass.

### Coming

The other monitor tests are **coming**: `icecore.schema_changes`, `icecore.column_anomaly` and `icecore.dimension_anomaly`. Their arguments will be documented here when they are merged.
