# Changelog

All notable changes to icecore are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and icecore uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version may change behaviour.

## [Unreleased]

### Added
- Telemetry written to the Snowflake event table through setup-created procedures (`ICECORE_EMIT`, `ICECORE_EMIT_RUN`), with `icecore.setup` and `icecore.doctor`.
- Run hooks: invocation and node spans, node and test results, query events, lineage.
- Monitors: `icecore.freshness`, `icecore.volume`, `icecore.schema_changes`, `icecore.column_anomaly`, `icecore.all_columns_anomaly`, `icecore.dimension_anomaly`, read from metadata where possible.
- Anomaly engines: native (seasonal median and MAD) and Cortex `ANOMALY_DETECTION`, with fallback and shadow scoring.
- Cost per node from `QUERY_ATTRIBUTION_HISTORY`, with backfill, settling and cost regressions.
- Root-cause attribution of failed tests.
- Views: `INVOCATIONS`, `NODE_EXECUTIONS`, `TEST_RESULTS`, `METRICS`, `SCHEMA_CHANGES`, `SILENCE_EVENTS`, `LINEAGE`, `CAPABILITIES`, and Elementary-compatible views.
- Missed-run detection (silence task) from `meta.sla`, and the `SLA_DECLARED` view listing every model with an SLA.
- Alerts: `ALERTS_FEED` (one row per incident, deduplicated per window), Snowflake alerts to email, Slack and Teams, repeat suppression, and acknowledgements through `ICECORE_ACK` (`icecore.acknowledge`).
- `layer` on `NODE_EXECUTIONS` and `LINEAGE` / `LINEAGE_LATEST` (`parent_layer`, `child_layer`) from the node's dbt fqn (`dbt.node.fqn`, `dbt.node.layer`), renamable with the `icecore.layers` var, and `threshold` on `METRICS` (the `max_lag` of `icecore.freshness`, in seconds).
- Streamlit app: Runs, Lineage, Cost and SLAs pages.
- Documentation: views reference, troubleshooting guide, CONTRIBUTING and roadmap.
