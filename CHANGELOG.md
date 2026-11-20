# Changelog

All notable changes to icecore are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and icecore uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version may change behaviour.

## [Unreleased]

### Added
- Cost estimates: a node whose Snowflake attribution has not landed at run end now gets a cost record at once, an estimate (`dbt.cost.estimated = true`). It comes from one guarded read of the current user's query history (no grant needed): execution time times the credits per hour of a standard warehouse of that size. An estimate writes no `credits` metric and is never scored. The backfill replaces it with Snowflake's attributed number (or its settled record) on a later run.
- `NODE_EXECUTIONS.credits_estimated` and `INVOCATIONS.credits_estimated` (true when the credits are, or include, an estimate; an attributed value always wins over the estimate of the same node), `INVOCATIONS.cost_pending_nodes` and the run-end LOG key `icecore.cost.pending_nodes` (nodes still waiting for attribution after the run). The console data passes them through without changing what is shown.
- A run warns in plain words when more nodes wait for attribution than `cost_backfill_limit` fills a run, saying their credits are estimates.
- Console: estimated credits are labelled. The Runs table shows an `estimate` pill, the Cost chart draws estimated runs as faded bars, the node page and lineage panel say when the latest credits are an estimate, and the Cost page counts the nodes still waiting for Snowflake. Averages, the half-on-half trend and per-model changes use attributed credits only.

### Changed
- Cost per node is now ON by default (`cost: true`). Turn it off with `vars: icecore: cost: false`. A role without `SNOWFLAKE.USAGE_VIEWER` still gets estimates; the attribution read keeps its single warning, which now names the grant (`GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE <dbt_role>`) and `cost: false`.
- The backfill treats a node whose only cost record is an estimate as still pending.
- `end_run` sends its batch to `ICECORE_EMIT_RUN` in `CALL`s of at most 768 KB instead of 512 KB. Each Python `CALL` pays about 2.5 s of start whatever its size, and a 178-node project spilled 18 KB into a second `CALL` that cost 2.3 s of warehouse time on every build. Records, views and columns are unchanged.

### Security
- Console: ECharts 5.5.0 to 6.1.0 (a cross-site scripting advisory), and the console build tooling (vite 5.4.21 to 6.4.6, with esbuild 0.25) to clear the open advisories. The charts keep the v5 look: the console loads ECharts' `v5` theme and turns off the v6 plot-area and axis-name repositioning, so every console page renders pixel-identical to before. The committed console build is rebuilt.

## [0.1.2] - 2026-11-20

### Fixed
- `icecore.freshness`: only a lag above the expected band is an anomaly (a table fresher than usual never is), and the band reaches at least 60 s above the usual lag, so seconds of jitter between builds no longer alert. In a week of scheduled runs on a real project, all 12 freshness anomalies were one of these two cases.
- `dbt compile` writes no telemetry. `snow dbt deploy` runs a compile that fires the hooks, so every deploy wrote an empty `compile` invocation.

### Changed
- Quickstart, dbt Projects on Snowflake: a vendored icecore needs its `tests/` folder (the generic tests), and a `package-lock.yml` written by a newer local dbt must be deleted before `snow dbt deploy`.

## [0.1.1] - 2026-11-05

### Added
- CI: the Snowflake sandbox suite runs on dbt 1.8 and on the latest dbt for every release (a pull request into `main`, or a manual dispatch with `release` ticked), never on other pull requests nor on a schedule. It runs in parallel, 4 parts per dbt version with the tests that share a setup kept together, then the timed tests (`test_scale`) alone: about 20 minutes. The Gate 1 spikes run only on the manual sandbox dispatch.
- Reference pages for every public macro and every generic test argument (`docs/reference/macros.md`, `tests.md`), generated from the `.yml` docs; generic test docs moved to `macros/tests/` so `dbt docs generate` shows them; a check fails for an undocumented public macro.
- Console: layer design checks inside the project, informative only (no score, status or incident changes). The Overview shows one line under the layer cards with the issues in a hover popover, and Models & sources gets a "Layer design" filter and column. Checks: a model reading a model of a layer further downstream, a model directly under `models/`, and, with the new var `layer_standard` (a list, recorded on `icecore.run_start` and read as `INVOCATIONS.layer_standard`), a model whose layer is not in the standard.

### Changed
- CI: the offline suite runs in parallel (pytest-xdist) over 5 runners, about 7 minutes instead of about 48, with the same tests. Contributors: both requirements files pin `pytest<9.2`, because pytest 9.2 drops pytest-xdist's `--dist loadgroup` groups.
- The SQL safety scan (`test_sql_safety.py`) now also flags `$${{ v }}$$` and any other `{{ }}` pasted into a `$$` body, `'%(v)s' % {...}`, `.format(` of any literal but `'{:,}'`, and a `log(` that is the SQL function (only the Jinja `log(` message is exempt); it no longer flags an apostrophe in a word, `/* it's */`, or `ilike '%s%'`. An icecore._sql call concatenated to anything else, a `%s` deeper in a literal, a `$$` made by a `{{ }}`, and a query inside a `log(` are flagged too. `| format(` and `%` of a `%s` literal (unless every argument is an `icecore._sql` call) and wrapped or filtered `_sql` concatenations are flagged too. No macro changed.

### Fixed
- With dbt-snowflake 1.12, dbt appends its query comment (profile, target, calling macro) to every statement, and it ended up in the text of icecore's views. `setup` now creates the views and the Elementary-compatible views through `EXECUTE IMMEDIATE`, so the comment stays out on any dbt version and running `setup` twice leaves identical views.
- Console Overview: the layer health strip follows the number of model layers. With 4 or more, the cards keep their width and wrap three per row instead of shrinking ("4 models, 19 tests" no longer wraps); 1 to 3 layers look as before.

## [0.1.0] - 2026-11-03

First public release.

### Added
- CI: a new push to a pull request cancels the run of its previous push.
- `make deploy-app` (`scripts/deploy_app.sh`): deploys the app to Streamlit in Snowflake from the file list of `streamlit/snowflake.yml`.
- Streamlit app: the Console (prototype D), one full-page component with Overview, Incidents, Tests and anomalies, Runs, Schedules and freshness, Schema changes, Cost, Lineage, Models and sources, and Settings. It runs locally (recommended) or as a Streamlit in Snowflake object, and its build folder ships with the app.
- `scripts/demo/build_rich_demo.sh` (`make rich-demo`): a sandbox-only demo built by 15 real dbt runs of `examples/shop` plus a few models, with scored metrics, row-count anomalies, a freshness breach, schema changes, four declared SLAs (one missed), failed and skipped models, and open, acknowledged and resolved incidents.
- The console data object (`streamlit/lib/console_data.py`) builds the prototype console's data from the views; the app's ALERTS_FEED read now includes `root_cause_unique_id` and `root_cause_kind`.
- `TEST_RESULTS.tested_unique_id` (the model or source a test is attached to, from `dbt.test.attached_node`) and `NODE_EXECUTIONS.root_cause_kind`. The Lineage page marks stale sources from the tested node, and the Elementary-compatible `model_unique_id` and `parent_model_unique_id` columns are filled.
- `icecore.setup` creates the alerts and the silence task when the `alerts` var is true, and resumes them (this uses warehouse time); `alert_resume` and `silence_task_resume` override it per object. With `alerts: false` it creates neither, and reports any existing one without touching it.
- `icecore.estimate_telemetry_cost` counts the records of the icecore monitor tests (own line), with `anomalies_per_run`, `assumed_columns` and `monitors` arguments.
- Telemetry written to the Snowflake event table through setup-created procedures (`ICECORE_EMIT`, `ICECORE_EMIT_RUN`), with `icecore.setup` and `icecore.doctor`.
- Run hooks: invocation and node spans, node and test results, query events, lineage.
- Monitors: `icecore.freshness`, `icecore.volume`, `icecore.schema_changes`, `icecore.column_anomaly`, `icecore.all_columns_anomaly`, `icecore.dimension_anomaly`, read from metadata where possible.
- The five monitor tests now score every metric against the test's own history, writing `METRICS.anomaly_*`; an anomaly warns by default (status `warn`) and fails the test with var `anomaly_severity: error`; a hard threshold still fails; per-test `engine`, `sensitivity`, `min_consecutive`, `lookback_days`, `min_points`, `shadow_engine`; var `monitor_score: false` turns it off. `freshness` fails on `max_lag` or an anomaly.
- Anomaly engines: native (seasonal median and MAD) and Cortex `ANOMALY_DETECTION`, with fallback and shadow scoring.
- Cost per node from `QUERY_ATTRIBUTION_HISTORY`, with backfill, settling and cost regressions.
- Root-cause attribution of failed tests.
- Views: `INVOCATIONS`, `NODE_EXECUTIONS`, `TEST_RESULTS`, `METRICS`, `SCHEMA_CHANGES`, `SILENCE_EVENTS`, `LINEAGE`, `CAPABILITIES`, and Elementary-compatible views.
- Missed-run detection (silence task) from `meta.sla`, and the `SLA_DECLARED` view listing every model with an SLA.
- Alerts: `ALERTS_FEED` (one row per incident, deduplicated per window), Snowflake alerts to email, Slack and Teams, repeat suppression, and acknowledgements through `ICECORE_ACK` (`icecore.acknowledge`).
- `layer` on `NODE_EXECUTIONS` and `LINEAGE` / `LINEAGE_LATEST` (`parent_layer`, `child_layer`) from the node's dbt fqn (`dbt.node.fqn`, `dbt.node.layer`), renamable with the `icecore.layers` var, and `threshold` on `METRICS` (the `max_lag` of `icecore.freshness`, in seconds).
- Documentation: views reference, troubleshooting guide, CONTRIBUTING and roadmap.

### Fixed
- A warehouse, role or database name with a single `$` (for example `my$wh`) stays unquoted and keeps its case; a NUL in a value is written `\x00` so it cannot cut a Scripting block; root cause on a database name with `$$` warns and skips instead of raising at on-run-end. The SQL safety test now flags every hand-built quote pattern (`%s`, `.format`, `{{ }}` next to a quote, quote lists, aliases of the retired literal helpers).
