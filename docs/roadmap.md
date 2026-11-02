# Roadmap

icecore is pre-release. This page says what is built, what comes next and what is only considered. It gives no dates. The plan sizes the work in weeks of part-time effort, and that is an estimate, not a promise. The detailed plan is in [the build roadmap](superpowers/plans/2026-10-30-icecore-roadmap.md). The decisions behind it are in the [design](design/scope-and-architecture.md).

Status words:

- **Merged** means the code is on `develop`. It is not released.
- **In progress** means a pull request is open or a story is under way.
- **Planned** means it is on the roadmap and not started.
- **Considered** means we have talked about it and have not planned it.

## Where we are

Gate 1 is decided. icecore writes telemetry through two procedures that `icecore.setup` creates, `ICECORE_EMIT` and `ICECORE_EMIT_RUN` ([conventions](conventions.md)). Nothing is released to dbt Hub yet.

## Before 1.0

The work is in six phases with three gates. A gate is a decision point where the owner checks the result before the next phase starts.

| Phase | What it delivers | Status |
| --- | --- | --- |
| 1. Foundations and spikes | Repository, sandbox, CI, and five spikes that measured the platform's limits. Ends with **Gate 1**, the write path | Merged. Gate 1 decided |
| 2. Run telemetry and read core | The `start_run` and `end_run` hooks, node results, query events, lineage, `setup`, `doctor`, and the core views | Merged: hooks, setup, doctor, failed-row samples and the views [`INVOCATIONS`, `NODE_EXECUTIONS`, `TEST_RESULTS`, `LINEAGE`, `LINEAGE_LATEST`, `CAPABILITIES`](reference/views.md) |
| 3. Monitors | Generic tests with static thresholds, and the `METRICS` and `SCHEMA_CHANGES` views. Ends with **Gate 2**, a pilot on a real dbt project | `icecore.freshness`, `icecore.volume` and the schema, column and dimension tests are merged. The pilot is not done |
| 4. Anomaly engines, cost, root cause | The native engine (merged as macros), the Cortex engine with native fallback, shadow scoring, cost per model, root-cause attribution | Merged: the native and Cortex engines with native fallback, shadow scoring, cost per node (opt-in with the `cost` var) and root-cause attribution (opt-in with the `root_cause` var) |
| 5. Control path and app | SLA and alert settings, Snowflake Alerts, missed-run detection, acknowledgements through `ICECORE_ACK`, the Streamlit in Snowflake app | The silence task and the app shell are merged. Alerts and acknowledgements through `ICECORE_ACK` are in progress (open pull requests) |
| 6. Compatibility and release | Elementary-compatible views, the runtime matrix, docs, the dbt Hub listing. Ends with **Gate 3**, the public release `1.0.0` | The Elementary-compatible views are merged. Docs are in progress. The rest is planned |

Pre-releases are tagged `0.1.0-alpha` (run telemetry), `0.2.0-beta` (monitors, engines, cost, root cause) and `0.3.0-beta` (alerts and app) on the way to `1.0.0`.

### Measured since Gate 1

`TRACE_LEVEL = ON_EVENT` is measured: it loses spans that carry no event, so icecore needs `ALWAYS`, set on its own schema and not on the database ([Gate 1](../spikes/GATE1.md#open-items), item 7).

### Open measurements

Some behaviour has not been measured yet, and `1.0.0` should not claim it before it is. All but the last are open items in [Gate 1](../spikes/GATE1.md#open-items):

- Where procedure records go from a database that has no event table of its own.
- Whether node spans written in a later `CALL` can be nested under the invocation span.
- How native dbt Projects records are identified.
- Behaviour on dbt Fusion, dbt Cloud and `EXECUTE DBT PROJECT`.
- The temporary-table privilege `edr report` needs.
- Reading `SNOWFLAKE.TELEMETRY.EVENTS_VIEW` through an icecore view owned by another role.
- Whether Cortex anomaly models can be shared across roles.
- Iceberg tables for the row-count monitors.

## After 1.0

These are ideas, not commitments.

- **A Native App for the read side.** The design keeps it as an option, because many teams move to Native Apps. It is considered, not planned.
- **Packaging on PyPI.** Considered, not planned.
- **Engine comparison charts in the app,** showing the native and Cortex scores side by side. Considered, not planned.

icecore targets Snowflake only, for good. We will not add other warehouses.

## How to influence it

Open an issue with the feature template. Say what you cannot do today. A request that fits the design and has a clear problem behind it moves up. See [CONTRIBUTING](../CONTRIBUTING.md).
