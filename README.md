# icecore

A Snowflake-native dbt observability package by Crithink. It writes run, test, metric and cost telemetry to the Snowflake event table, monitors freshness, volume and schema from metadata without scanning data, scores anomalies with a native SQL engine or Snowflake Cortex, and ships a Streamlit in Snowflake app.

**Status:** Phase 1 (foundations and spikes). Not usable yet.

- Design: [docs/design/scope-and-architecture.md](docs/design/scope-and-architecture.md)
- Roadmap: [docs/superpowers/plans/2026-10-30-icecore-roadmap.md](docs/superpowers/plans/2026-10-30-icecore-roadmap.md)

## Development

```bash
cd integration_tests
python3 -m venv .venv && .venv/bin/pip install -r requirements-floor.txt
.venv/bin/pytest -m "not sandbox"      # offline checks
.venv/bin/pytest -m sandbox            # needs SNOWFLAKE_* variables and a bootstrapped sandbox
```

Licence: Apache-2.0.
