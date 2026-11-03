# icecore build roadmap

**Spec:** `Scope & Architecture — Snowflake-native dbt Observability Package.md` (moves to `docs/design/scope-and-architecture.md` in the repo in Phase 1, Task 1).
**Repo:** `CRITHINK-DEV/icecore` (public).

Six phases, about 15 weeks of part-time effort, three gates (section 9 of the spec). Only Phase 1 has a detailed plan. Each later phase is planned after the gate before it, because Gate 1 decides how telemetry is written, and that changes the macros of every phase after it.

| Phase | Weeks | Delivers (floorplan parts) | Ends with |
|---|---|---|---|
| 1. Foundations and spikes | 1–2 | Repo scaffold, sandbox harness, CI; spikes S1–S5 with measured results | **Gate 1**: write encoding chosen; spec sections 3, 4 and 8 updated |
| 2. Run telemetry and read core | 3–5 | Write path / Run telemetry (`start_run`, `end_run`, `emit`, `attributes`, node results, query events, samples, lineage); Setup and doctor; core views `invocations`, `node_executions`, `test_results`, `lineage`, `capabilities` | Tagged `0.1.0-alpha` |
| 3. Monitors | 6–8 | Monitors (`metadata`, `schema`, `columns`, `emit_metric`); tests `icecore.freshness`, `icecore.volume`, `icecore.schema_changes`, `icecore.column_anomaly`, `icecore.dimension_anomaly`, `icecore.all_columns_anomaly` with static thresholds; views `metrics`, `schema_changes` | **Gate 2**: pilot on a real dbt project (telemetry and monitors only) |
| 4. Anomaly engines, cost, root cause | 9–11 | `score`, `native`, `cortex`, `shadow`, `get_metric_history`; `attach_cost`; `walk_upstream`, `classify_cause` | Tagged `0.2.0-beta` |
| 5. Control path and app | 12–13 | `collect_sla_config`, `render_alert_sql`, `dedup_window` (with acks), `render_silence_task_sql`, `refresh_cortex_models`; views `silence_events`, `alerts_feed`; Streamlit app (6 pages, `ack`) | Tagged `0.3.0-beta` |
| 6. Compatibility and release | 14–15 | Elementary-compatible views + S4 verification; runtime matrix (Core 1.8 / latest, Fusion 2.x, `EXECUTE DBT PROJECT`); docs; dbt Hub listing | **Gate 3**: public release `1.0.0` |

Plans:
- Phase 1: `2026-10-30-icecore-phase1-foundations-and-spikes.md`
- Phases 2–6: written after the gate that precedes each one.
