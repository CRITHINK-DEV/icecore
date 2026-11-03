# Changelog

All notable changes to icecore are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and icecore uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version may change behaviour.

## [Unreleased]

### Added
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
- Streamlit app: Runs, Lineage, Cost and SLAs pages.
- Documentation: views reference, troubleshooting guide, CONTRIBUTING and roadmap.

### Fixed
- A warehouse, role or database name with a single `$` (for example `my$wh`) stays unquoted and keeps its case; a NUL in a value is written `\x00` so it cannot cut a Scripting block; root cause on a database name with `$$` warns and skips instead of raising at on-run-end. The SQL safety test now flags every hand-built quote pattern (`%s`, `.format`, `{{ }}` next to a quote, quote lists, aliases of the retired literal helpers).
