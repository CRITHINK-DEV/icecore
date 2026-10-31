# Scope & Architecture — Snowflake-native dbt Observability Package

Oct 27, 2026 · updated Oct 30, 2026 · @anouar · Draft for review

## 1. Vision and positioning

**icecore** is a Snowflake-only dbt observability package that writes nothing itself, scans nothing it doesn't have to, and runs nothing outside Snowflake. Elementary is warehouse-agnostic, so everything it does is lowest-common-denominator SQL: it inserts its own tables from hooks (table locks under concurrent runs), rescans data to compute metrics, hand-rolls anomaly scoring, and needs a Python CLI to render anything. Going Snowflake-only removes that machinery instead of re-implementing it.

**Three design principles**

1. **Write nothing yourself.** All telemetry (run results, test results, metrics, samples) goes through the Snowflake event table via `SYSTEM$LOG` / spans. No DML, no locks, no tables to create or migrate, and logs survive a failed or rolled-back run.
2. **Scan nothing you don't have to.** Freshness, volume and schema signals come from Snowflake metadata (`INFORMATION_SCHEMA`, `SYSTEM$LAST_CHANGE_COMMIT_TIME`, `SHOW COLUMNS`) at zero warehouse cost. Column-level metrics scan only changed micro-partitions (`CHANGES` / change tracking).
3. **Run nothing outside Snowflake.** Alerting (Snowflake Alerts + notification integrations), scheduling (tasks), ML (Cortex, optional) and UI (Streamlit in Snowflake) are all native. No CLI, no external scheduler, no Python runtime to operate.

**Kept from Elementary (because it is right)**

- Run / test result models and the invocation concept
- Failed-row samples on test failures
- Lineage from the dbt manifest
- Test family taxonomy: volume, freshness, schema, column, dimension
- YAML-first configuration via `meta` and test arguments

**New (not in Elementary OSS, and not in any dbt package today)**

| Capability | What it does | Why nobody has it |
| --- | --- | --- |
| Zero-scan monitors | Freshness, row count and schema diff from metadata, no data scan | Requires Snowflake-specific metadata functions |
| Cost per model / test / run | Node `query_id` joined to `QUERY_ATTRIBUTION_HISTORY`; cost regressions become an anomaly type | Elementary has no cost; dbt-snowflake-monitoring has no run results |
| Silence detection | A Snowflake task compares declared schedules (`meta.sla`) to the event table and alerts on runs that did not happen | Hook-based tools cannot see a missing run |
| Root-cause attribution | On a test failure, walk the manifest DAG to the nearest upstream failed / stale / schema-changed node and name it in the alert | Elementary Cloud only |
| Unified inside/outside view | Same attribute namespace as Snowflake's native dbt Projects telemetry, so one set of views covers dbt Cloud, Airflow and `EXECUTE DBT PROJECT` | New Snowflake feature, no package aligned with it yet |
| Native traces | One trace per invocation, one span per node, queries as child spans, visible in Snowsight's trace viewer | Event-table only |
| Dual-engine anomaly detection | Native SQL engine always; Cortex ML engine when allowed; shadow scoring to compare them | Elementary has one hand-rolled engine |

**Target users**: data platform teams running dbt on Snowflake who want observability without operating another tool, and who often cannot install external SaaS or CLIs in regulated environments (banking, insurance, industry).

## 2. Decisions and open questions

Decided on 27 Oct 2026:

| Topic | Decision |
| --- | --- |
| Platform | Snowflake only, permanently. No abstraction seam for other warehouses. |
| Form factor | dbt package (write + read layer) plus a Streamlit in Snowflake app. Native App packaging kept as an option for the read side, since many clients are moving to Native Apps. |
| Delivery | Incremental, step by step; anomaly detection is in scope, not deferred. |
| Ownership | Open source, published by Crithink. |
| Anomaly scoring | Two engines behind one contract: `native` (pure SQL, always available) and `cortex` (Cortex ML, when the account allows it). Cortex is encouraged via shadow scoring, never required. |
| Storage | Event table for all telemetry. No package-owned tables. Hybrid tables are the documented fallback only if spike S1 fails. |

Decided on 30 Oct 2026:

| Topic | Decision |
| --- | --- |
| Name | **icecore**: scientists drill ice cores to read history layer by layer, as the package reads dbt history from the event table. One name for the repo (`crithink-dev/icecore`), the dbt Hub package, the event-table `SCOPE`, the attribute prefix and the app title. Free on dbt Hub and on GitHub as `crithink-dev/icecore`; no "snow", "flake" or "dbt" in it, so no clash with Snowflake or dbt Labs marks. `icecore` is taken on PyPI by an abandoned 2018 project, so Python tooling, if any, ships as `icecore-dbt` (name reserved on PyPI on 30 Oct 2026 with an empty 0.0.1 release). Nothing in the design is pip-installed today. |
| Licence | Apache-2.0, same as dbt\_artifacts and Elementary's package. |
| Snowflake edition | Any edition with event tables. Standard uses the account's active event table (by default `SNOWFLAKE.TELEMETRY.EVENTS`); Enterprise can also get a dedicated database-level event table, which Snowflake limits to Enterprise. The `SCOPE` filter makes a shared table safe either way. `setup` detects the edition and never changes the account-level event table itself (that needs `ACCOUNTADMIN`): it prints the statement for an admin. |
| dbt version | `require-dbt-version: [">=1.8.0", "<3.0.0"]`: every maintained dbt Core, dbt Cloud, Fusion 2.x and dbt Projects on Snowflake, so anyone can try it. Older Core versions are out of support and would widen the CI matrix for little gain. CI runs the oldest and newest supported Core. |
| Streamlit app | Ships in `streamlit/` inside the package repo, so users find everything in one place. |
| Capabilities | Recorded in-band, as Elementary does with `dbt_invocations`: `setup` and `doctor` emit a capabilities `LOG`, every invocation span carries the package version, dbt version and engine settings, and a `capabilities` view exposes both to the app. |
| Acknowledgements | An acknowledged `incident_key` stops notifying: `alerts_feed` marks it acknowledged and `dedup_window` drops it. A recurrence with a new `incident_key` alerts again. |

Open questions: none left at this level. The remaining unknowns are the spikes in section 8 (S1, S3 and S4 can change the design).

## 3. Architecture overview

The package has one store (the event table) and three paths around it: a write path from dbt, a read path for people, and a control path for alerts and tasks.

&#91;embedded content: architecture · 3 sources, 1 store, 3 consumers\]

dbt runs outside Snowflake emit telemetry through package macros; native dbt Projects runs are emitted by Snowflake itself; both land in the same event table with the same attribute names, and views, alerts and tasks read it from there.

**Write path.** Package macros run in three places: an `on-run-start` hook (invocation span opens), node-level hooks and test macros (metrics, results, samples), and an `on-run-end` hook (per-node results, root-cause pass, invocation span closes). Every emission is an anonymous Snowflake Scripting block calling `SYSTEM$LOG_*` / `SYSTEM$ADD_EVENT`, executed through the adapter's normal `run_query`. There is no INSERT anywhere in the package.

**Read path.** A `setup` macro creates views over the active event table in a schema the user chooses. Views are the only public interface; the Streamlit app and any external BI tool read views, never the raw table. A second set of views reproduces Elementary's table names and columns so existing dashboards and, if spike S4 passes, the `edr` report keep working.

**Control path.** Snowflake Alerts run on a schedule against the views and push through a notification integration (email, Slack/Teams via webhook). A scheduled task implements silence detection by comparing declared SLAs to the last successful invocation per model. An optional task refreshes Cortex anomaly models from metric history.

**What the package does not own**: no tables, no stages, no external compute, no secrets (webhooks live in Snowflake notification integrations / secrets objects).

## 4. Event-table conventions

All package emissions are identified by one `SCOPE` name and one attribute prefix, and they reuse Snowflake's own attribute names wherever Snowflake already defines one. The event table has a fixed schema (`TIMESTAMP`, `START_TIMESTAMP`, `OBSERVED_TIMESTAMP`, `TRACE`, `RESOURCE`, `RESOURCE_ATTRIBUTES`, `SCOPE`, `SCOPE_ATTRIBUTES`, `RECORD_TYPE`, `RECORD`, `RECORD_ATTRIBUTES`, `VALUE`, `EXEMPLARS`), so all package data lives in `RECORD_ATTRIBUTES` and `VALUE` (VARIANT).

**Record types used**

| Record type | Used for | One row per |
| --- | --- | --- |
| `SPAN` | Invocation (root span), node execution (child span) | Invocation, node |
| `SPAN_EVENT` | Query executed by a node, with its `query_id` | Query |
| `LOG` (INFO / WARN / ERROR) | Node result, test result, schema diff, root-cause attribution, silence-detector verdict, lineage edges, capabilities snapshot (from `setup` / `doctor`), alert acknowledgement (from the app) | Event |
| `METRIC` | Monitor values (row count, freshness lag, null rate, column stats, cost in credits) and anomaly scores | Metric per table/column per run |
| `LOG` (DEBUG) | Failed-row samples (payload capped, see S2) | Test failure |

**Attribute namespace** (`RECORD_ATTRIBUTES` keys: `dbt.` for facts about the dbt run, `icecore.` for the package's own, Snowflake's own keys reused as-is)

| Key | Example | Note |
| --- | --- | --- |
| `snow.executable.type` | `DBT_PROJECT` | Reused from Snowflake's native dbt telemetry so one filter covers both sources |
| `snow.query.id` | `01c5a039-...` | Set by Snowflake on native runs; we set it from the adapter response on external runs |
| `dbt.invocation_id` | `a1b2-...` | From `invocation_id` |
| `dbt.runtime` | `core` / `cloud` / `fusion` / `snowflake_native` | Where dbt ran |
| `dbt.command`, `dbt.target`, `dbt.project` | `build`, `prod`, `finance_dw` |  |
| `dbt.node.unique_id`, `dbt.node.type`, `dbt.node.name`, `dbt.node.materialization` | `model.finance_dw.fct_orders`, `model`, ... |  |
| `dbt.node.relation` | `DB.SCHEMA.FCT_ORDERS` | Fully qualified, for joins to metadata views |
| `dbt.result.status`, `dbt.result.rows_affected`, `dbt.result.execution_time_s`, `dbt.result.message` | `success`, `1200`, `4.2`, `...` |  |
| `dbt.test.name`, `dbt.test.type`, `dbt.test.column`, `dbt.test.severity`, `dbt.test.failures` | `volume_anomaly`, `warn`, `3` |  |
| `dbt.metric.name`, `dbt.metric.column`, `dbt.metric.dimension` | `row_count`, `null_rate`, `country=FR` | For `METRIC` records |
| `dbt.anomaly.engine`, `dbt.anomaly.expected`, `dbt.anomaly.lower`, `dbt.anomaly.upper`, `dbt.anomaly.score`, `dbt.anomaly.is_anomaly` | `native`, ... | Same keys whatever engine scored it |
| `dbt.cost.credits`, `dbt.cost.warehouse` | `0.021`, `TRANSFORM_WH` | Joined post-run from `QUERY_ATTRIBUTION_HISTORY` by `snow.query.id` |
| `dbt.root_cause.unique_id`, `dbt.root_cause.kind` | `model.x.stg_orders`, `upstream_failure` / `stale_source` / `schema_change` | Set by the on-run-end DAG walk |
| `icecore.version`, `icecore.schema_version` | `0.1.0`, `1` | Lets views handle payload changes |
| `dbt.version`, `icecore.anomaly_engine`, `icecore.shadow_engine` | `1.10.4`, `auto`, `cortex` | On the invocation span, so every run records what it ran with (as Elementary's `dbt_invocations` does) |
| `icecore.capability.edition`, `icecore.capability.event_table`, `icecore.capability.cortex` | `ENTERPRISE`, `OBS_DB.TELEMETRY.EVENTS`, `true` | On the capabilities `LOG` emitted by `setup` / `doctor` |
| `icecore.ack.incident_key`, `icecore.ack.by` | `fct_orders:volume:2026-10-30`, `JDOE` | On an acknowledgement `LOG` from the app |

**Rules**

- One `SCOPE` name for the package (`icecore`), set on every emission; views filter on it, so other telemetry in a shared event table is never read.
- `VALUE` carries the human-readable message for logs and the numeric value for metrics; everything structured goes in attributes, never parsed out of the message.
- Payloads are capped at a configurable size (default 64 KB, see spike S2); samples are truncated row-wise with a `dbt.sample.truncated = true` flag rather than failing the hook.
- Timestamps come from the dbt result (`START_TIMESTAMP` from the node's timing), not from the ingestion time, so lag never shifts the analytics.
- Every emission is wrapped in `try / except` inside the macro: a telemetry failure logs a dbt warning and never fails the run.

## 5. Functional modules and macro inventory

Eight modules, each independently switchable with a var, so a customer can adopt run telemetry alone on day one and add monitors later.

| Module | Entry points (user-facing) | Internal macros | Reads | Emits |
| --- | --- | --- | --- | --- |
| **Setup** | `run-operation icecore.setup`, `icecore.doctor` | `detect_capabilities` (edition, Cortex, event table), `create_views`, `create_alerts`, `create_tasks`, `set_telemetry_levels` | `SHOW PARAMETERS`, `SHOW FUNCTIONS` | A capabilities `LOG` (edition, active event table, Cortex, telemetry levels); also prints the flags for `dbt_project.yml` and any statement that needs `ACCOUNTADMIN` |
| **Run telemetry** | `on-run-start: icecore.start_run()`, `on-run-end: icecore.end_run(results)` | `emit`, `open_span`, `close_span`, `emit_node_result`, `emit_query_event`, `collect_query_ids`, `emit_sample`, `emit_lineage` | dbt `results`, `graph`, adapter responses | Invocation span (with package version, dbt version and engine settings), node spans, node results, query events, failed-row samples, lineage edges |
| **Metadata monitors (zero-scan)** | Tests `icecore.freshness`, `icecore.volume`, `icecore.schema_changes` | `get_last_change_time`, `get_row_count_metadata`, `get_columns_signature`, `diff_schema` | `INFORMATION_SCHEMA.TABLES`, `SYSTEM$LAST_CHANGE_COMMIT_TIME`, `SHOW COLUMNS` | METRIC rows + a LOG on breach |
| **Column monitors (incremental scan)** | Tests `icecore.column_anomaly`, `icecore.dimension_anomaly`, `icecore.all_columns_anomaly` | `get_change_window`, `compute_column_metrics`, `compute_dimension_metrics` | Target table via `CHANGES` since last run (fallback: timestamp column) | METRIC rows |
| **Anomaly engines** | Var `anomaly_engine: auto` / per-test `engine:` | `score` (dispatcher), `native__score`, `cortex__score`, `get_metric_history`, `shadow_score` | Metric history from the event table views | Anomaly attributes on the metric; a LOG when `is_anomaly` |
| **Cost** | Enabled by var; no user macro | `attach_cost` (run at on-run-end, with a lag-tolerant backfill in the next run) | `QUERY_ATTRIBUTION_HISTORY` by `snow.query.id` | `dbt.cost.*` on node spans; `cost` METRIC per node |
| **Root cause** | Enabled by var | `walk_upstream`, `classify_cause` | dbt `graph` + this run's results + latest freshness/schema LOGs | `dbt.root_cause.*` on failed test LOGs |
| **Alerts and silence** | `setup` creates them; config in `meta.alerts`, `meta.sla` | `collect_sla_config`, `render_alert_sql`, `render_silence_task_sql`, `dedup_window` (skips acknowledged incidents), `refresh_cortex_models` | Views | Snowflake `ALERT` objects, the silence `TASK`, the optional Cortex refresh `TASK`, notification integration calls |

**Test ergonomics** (same shape as dbt tests; sensible defaults so a bare test works):

```yaml
models:
  - name: fct_orders
    meta:
      sla: {expected_every: 1h, grace: 15m}
      alerts: {channel: data-platform, severity: warn}
    tests:
      - icecore.volume            # zero-scan, native engine by default
      - icecore.freshness:
          max_lag: 2h
      - icecore.schema_changes
    columns:
      - name: amount
        tests:
          - icecore.column_anomaly:
              metrics: [null_rate, avg, max]
              engine: cortex       # overrides the project default
              sensitivity: medium
```

**Elementary parity checklist** (what a migrating team will look for):

- [x] Run and test results per invocation
- [x] Failed-row samples
- [x] Volume / freshness / schema / column / dimension anomalies
- [x] Lineage
- [x] Slack alerts
- [ ] Elementary's HTML report (replaced by the Streamlit app; compatible views keep `edr` working if S4 passes)
- [ ] Source freshness results (dbt `source freshness` runs the on-run-end hook too; to confirm in S3)

## 6. Anomaly engine contract

One contract, two engines: every engine takes the same metric history and returns the same six fields, so views, alerts and the app never know which one scored a result.

**Input**: a metric series for one (table, column, dimension, metric) key, read from the event table views: `(observed_at, value)` rows for the lookback window (default 30 days, min 7 points), plus the current value computed in this run.

**Output** (written as `dbt.anomaly.*` attributes on the current METRIC record):

| Field | Type | Meaning |
| --- | --- | --- |
| `engine` | string | `native` or `cortex`, whichever actually scored |
| `expected` | number | Point forecast for this observation |
| `lower`, `upper` | number | Acceptance band at the configured sensitivity |
| `score` | number | Signed distance outside the band, 0 inside; comparable across engines |
| `is_anomaly` | boolean | `score != 0` after the configured `min_consecutive` rule |

**`native` engine** (always available, default on first install)

- Seasonal baseline: history bucketed by hour-of-day and day-of-week when the series is dense enough (default: more than 4 weeks), otherwise a flat window.
- Robust statistics: median and MAD instead of mean and standard deviation, so one past outlier does not widen the band.
- Optional EWMA on the baseline for trending metrics (`trend: true`).
- Sensitivity maps to a MAD multiplier: `low` 4, `medium` 3, `high` 2.
- Cost: one small aggregate over at most a few thousand rows of history, executed in the test's own query.

**`cortex` engine** (when the account allows it)

- `SNOWFLAKE.ML.ANOMALY_DETECTION` trained per table on all its metric series at once (multi-series), with `prediction_interval` mapped from sensitivity (`low` 0.99, `medium` 0.95, `high` 0.90).
- Models are refreshed by the optional scheduled task, not at test time, so tests stay fast; a missing or stale model falls back to `native` for that run and logs why.
- Requires the `SNOWFLAKE.CORTEX_USER` / ML privileges and a region where the function exists; `setup` detects both.

**Selection** (`anomaly_engine` var, overridable per test):

| Setting | Behaviour |
| --- | --- |
| `native` | Native only, Cortex never called |
| `cortex` | Cortex; falls back to native when the model is unavailable or history is too short, and records the fallback |
| `auto` (default) | Cortex if detected at setup and the series has at least 14 days of history, else native |

**Shadow scoring** (`shadow_engine: cortex`): the configured engine decides `is_anomaly`; the shadow engine's output is emitted alongside as `dbt.anomaly.shadow.*`. The Streamlit app shows, per table, how many alerts each engine would have raised and where they disagree, so a team can switch engines on evidence rather than on a claim. Costs nothing when Cortex is off.

**Explainability**: every anomaly LOG carries the band, the baseline window and the engine, so a reviewer can reproduce a native verdict by hand and see a Cortex verdict's interval.

## 7. Read layer and Streamlit app

Views are the public interface; the raw event table is an implementation detail. `setup` creates them in a schema the user picks (default `<target_db>.ICECORE_OBS`), all filtered on the package `SCOPE` and typed from `RECORD_ATTRIBUTES`.

**Core views** (package-native names)

| View | Grain | Main columns |
| --- | --- | --- |
| `invocations` | One row per dbt run | `invocation_id`, `runtime`, `command`, `target`, `started_at`, `ended_at`, `status`, `nodes_total`, `nodes_failed`, `credits` |
| `node_executions` | One row per node per run | `invocation_id`, `unique_id`, `type`, `materialization`, `status`, `rows_affected`, `execution_time_s`, `query_id`, `credits`, `root_cause_unique_id` |
| `test_results` | One row per test per run | `invocation_id`, `test_unique_id`, `model_unique_id`, `column`, `test_type`, `status`, `failures`, `severity`, `engine`, `expected`, `lower`, `upper`, `score` |
| `metrics` | One row per metric per run | `relation`, `column`, `dimension`, `metric`, `observed_at`, `value`, anomaly fields |
| `schema_changes` | One row per detected change | `relation`, `change_kind` (added / removed / type\_changed), `column`, `from_type`, `to_type`, `detected_at` |
| `silence_events` | One row per missed run | `unique_id`, `expected_at`, `grace`, `detected_at`, `last_success_at` |
| `lineage` | One row per edge | `parent_unique_id`, `child_unique_id`, from the latest manifest emission |
| `alerts_feed` | One row per alertable event, deduped | Union of failed tests, anomalies, schema changes, silence, cost regressions; `incident_key` for grouping; `acknowledged_at` / `acknowledged_by` from ack records |
| `capabilities` | One row per detection, latest first | `detected_at`, `edition`, `event_table`, `cortex_available`, `log_level`, `trace_level`, plus `icecore_version` and `dbt_version` from recent invocations |

**Elementary-compatible views** (opt-in via `setup(compat='elementary')`): `dbt_invocations`, `dbt_run_results`, `elementary_test_results`, `dbt_models`, `dbt_tests`, `dbt_sources`, `data_monitoring_metrics`, `schema_columns_snapshot` with Elementary's column names and types. Purpose: existing dashboards keep working, and (spike S4) `edr report` may render against them unchanged.

**Performance**: views carry `RECORD_TYPE` and `SCOPE` predicates first so pruning on the event table works; `setup` recommends a clustering key on the custom event table (`TIMESTAMP::date, RECORD_TYPE`) when the account creates one; for large estates, `setup` can optionally materialize `metrics` as a dynamic table (the only Snowflake object besides views the package may create, and only on request).

**Streamlit in Snowflake app** (reads the core views; deployed by `setup(app=true)` or manually)

| Page | Shows | Decision it supports |
| --- | --- | --- |
| Runs | Invocation list, status, duration, credits; drill to node timeline (spans) | Is the platform healthy today, what got slower |
| Tests and anomalies | Failures and anomalies with band charts, engine badge, shadow comparison | What to act on, which engine to trust |
| Lineage | DAG with failure / stale / schema-change overlay, root-cause highlight | Where the problem started |
| Cost | Credits per model, per run, trend, top regressions | What to optimise |
| SLAs | Declared schedules vs actual, silence events | Which contracts are breached |
| Settings | Detected capabilities (from the `capabilities` view), package and dbt versions per run, engine selection, alert channels, retention | Is the package configured right |

The app has no write path except acknowledging alerts, which is a comment-style `LOG` emission through the same event-table pipeline (an `ack` record keyed by `incident_key`), so the package still owns no tables. `alerts_feed` reads those records and `dedup_window` stops notifying for an acknowledged `incident_key`; a recurrence with a new key alerts again. A Native App wrapper of the same app is a packaging task, not a redesign.

## 8. Technical spikes before building

Five checks, about two days in total, run on a sandbox account with a custom event table. S1 is the premise of the whole package; if it fails, the write path switches to hybrid tables and sections 3 and 4 are revised before any module is written.

| # | Question | How to test | Pass | Fail → fallback |
| --- | --- | --- | --- | --- |
| S1 | Do `SYSTEM$LOG` / `SYSTEM$ADD_EVENT` calls from an **anonymous** Snowflake Scripting block (sent through dbt's `run_query`) land in the event table with usable `RESOURCE_ATTRIBUTES` (`snow.query.id`, session, user, role, warehouse)? | Emit 100 logs and 10 spans from an anonymous block via dbt-snowflake; query the event table | Rows present, `snow.query.id` matches the block's query, attributes queryable | Write path uses a hybrid table (row locks); views unchanged |
| S2 | What is the maximum payload per log message and per attribute map, and what happens above it? | Emit messages of 8 KB, 64 KB, 256 KB, 1 MB; check truncation or error | Documented limit; truncation flagged, never a failed hook | Samples chunked across several LOG records with a `dbt.sample.part` index |
| S3 | Ingestion lag under load, and whether the on-run-end hook fires for `dbt source freshness` and `dbt test` alone | 20 concurrent `dbt build` runs each emitting \~500 records; measure `OBSERVED_TIMESTAMP - TIMESTAMP` p50/p95; run each command type | p95 lag under 60 s; hook fires for all commands used | Anomaly history read with a configurable delay; silence task grace raised |
| S4 | Do Elementary-compatible views satisfy `edr report`? | Install `elementary-data`, point its profile at the compat views, run `edr report` | Report renders with runs, tests, lineage | Compat views stay for dashboards; the report is Streamlit-only |
| S5 | Zero-scan metrics are reliable: does `INFORMATION_SCHEMA.TABLES.ROW_COUNT` and `SYSTEM$LAST_CHANGE_COMMIT_TIME` reflect writes within seconds, including for dynamic tables and Iceberg tables? | Write to each table kind, read metadata immediately and after 60 s | Row count and change time correct within 60 s for standard tables; behaviour for dynamic / Iceberg documented | Per-kind fallback to a `COUNT(*)` over `CHANGES` |

All five produce a short note in the repo (`spikes/S1.md` ...) with the SQL used and the numbers found, so the limits in section 4 are measured, not assumed.

## 9. Delivery plan

Six phases over roughly fifteen weeks of part-time effort, with a gate after the spikes, after the first pilot, and before the public release. Weeks are effort estimates, not calendar commitments.

&#91;embedded content: roadmap · 6 phases, 3 gates, \~15 weeks\]

Each phase ships a tagged pre-release usable on its own; the pilot at gate 2 runs on a real project with run telemetry and monitors only, before anomaly detection is added.

**Repository layout** (one repo, `crithink-dev/icecore`)

```text
icecore/
  dbt_project.yml
  macros/
    setup/            setup, doctor, detect_capabilities, set_telemetry_levels,
                      create_views, create_alerts, create_tasks, deploy_app
    telemetry/        start_run, end_run, emit, attributes, spans, node results,
                      query events, samples, lineage
    monitors/         metadata (zero-scan), columns (CHANGES), schema, emit_metric
    engines/          score dispatcher, metric history, native, cortex, shadow
    cost/             attach_cost
    root_cause/       walk_upstream, classify_cause
    views/            core views (one macro per view) + shared event filter
    compat/           elementary view definitions
    control/          alert and task SQL: SLA config, alerts, dedup, silence, Cortex refresh
  tests/generic/      generic tests exposed to users
  streamlit/          Streamlit in Snowflake app (one file per page, lib/ for queries,
                      charts, lineage DAG, ack)
  integration_tests/  a sample dbt project + pytest harness against a sandbox account
  spikes/             S1..S5 notes and SQL
  docs/               quickstart, configuration, conventions, migration from Elementary
  .github/workflows/  CI against the sandbox account
```

**Licensing and publishing**: Apache-2.0 (decided 30 Oct; permissive, patent grant, same as dbt\_artifacts; Elementary's package is Apache-2.0 too, which keeps any borrowed idea clean). Published on dbt Hub under the `crithink-dev` namespace (dbt Hub namespaces follow the GitHub org); releases follow semver with the event-table `icecore.schema_version` bumped only on breaking attribute changes.

**Naming**: **icecore** (decided 30 Oct, see section 2). It meets the constraints: short, works as a dbt package name (`icecore.volume`), a Snowflake `SCOPE` value and a Streamlit app title, and avoids Snowflake and dbt Labs product names. Checked 30 Oct 2026: free on dbt Hub and as `crithink-dev/icecore` on GitHub (other unrelated `icecore` repos exist, none in data tooling). On PyPI `icecore` is taken by an abandoned 2018 C++-bindings project, so Python tooling uses `icecore-dbt`, reserved on 30 Oct 2026. Trademark search still to do before the first public release.

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Anonymous-block logging does not carry the attributes we need (S1) | The whole write path | Hybrid-table write path as designed fallback; views unchanged, so nothing above the write layer moves |
| Ingestion lag higher than expected (S3) | Alerts late; anomaly history missing the last run; silence detector false positives | Lag-aware views (`observed_at` vs `TIMESTAMP`), configurable history delay, grace period on SLAs defaulting to 2x measured p95 |
| Shared event table is noisy or restricted | Views slow; security teams refuse read access to a table that also holds other apps' logs | `SCOPE` filter first in every view; on Enterprise, recommend a dedicated database-level event table with its own grants; on Standard, read through `EVENTS_VIEW` with the `EVENTS_VIEWER` role (row access policies on it are Enterprise-only); both patterns documented |
| Telemetry cost surprises | Serverless ingestion credits on large estates | `setup` prints an estimate from node count and run frequency; payload caps; sample emission off by default on `warn` tests |
| Cortex not allowed or not in region | No ML engine | `native` is the default and the guaranteed baseline; Cortex is additive and detected, never assumed |
| Zero-scan metadata lags or differs by table kind (S5) | Wrong freshness/volume verdicts | Per-kind fallback to a `CHANGES`-based count; documented matrix of table kinds |
| Elementary-compat views drift from Elementary's schema | Broken dashboards for migrating teams | Compat views are versioned against a named Elementary release; integration test runs `edr report` in CI |
| dbt Fusion or dbt Projects on Snowflake change hook behaviour | Hooks not firing, results shape changing | Integration tests on Core 1.8 and the latest Core, Fusion 2.x and `EXECUTE DBT PROJECT`; the native path needs no hooks, which is the point of the unified namespace |
| Scope creep toward a product before the core is solid | Delay | Gates in section 9; nothing beyond the current phase is started before the gate |
| Open-source maintenance load | Issues and PRs without a team | Narrow platform (Snowflake only) keeps the surface small; CI against a sandbox account; a clear `CONTRIBUTING.md` and a public roadmap |

**Sources consulted for this draft**: [Monitor dbt Projects on Snowflake](https://docs.snowflake.com/en/user-guide/data-engineering/dbt-projects-on-snowflake-monitoring-observability), [dbt Projects on Snowflake limitations](https://docs.snowflake.com/en/user-guide/data-engineering/dbt-projects-on-snowflake-limitations.html), [Event table overview](https://docs.snowflake.com/en/developer-guide/logging-tracing/event-table-setting-up), [LOCK\_WAIT\_HISTORY](https://docs.snowflake.com/en/sql-reference/account-usage/lock_wait_history), [brooklyn-data/dbt\_artifacts issues](https://github.com/brooklyn-data/dbt_artifacts/issues), [Elementary dbt artifacts](https://docs.elementary-data.com/data-tests/dbt/dbt-artifacts), [Elementary troubleshooting](https://docs.elementary-data.com/oss/general/troubleshooting), [Elementary issue #1419](https://github.com/elementary-data/elementary/issues/1419).
