# Gate 1: how icecore writes telemetry

Decision record. Decided on 31 Oct 2026 by the owner, Anouar Zbaida, after spikes S1, S2, S4 and S5. Story cb2e80f0 (S12).
Spike S3 has not run yet; what depends on it is listed under [Open items](#open-items).

Every number below comes from a spike note or its committed results file, cited as `S<n>.md` or `results/s<n>.json` with
the key. The spec sections this decision changes are 2, 3, 4, 5, 7, 8 and 10 of `docs/design/scope-and-architecture.md`.

## Decision

**Option B: two persisted procedures.** `icecore.setup` creates them in `<target_db>.ICECORE_OBS`, both `EXECUTE AS OWNER`.
Hooks and tests emit by `CALL`ing them through `run_query`, and every `CALL` is guarded.

| Procedure | Language | Writes | `SCOPE` | Called | Measured |
|---|---|---|---|---|---|
| `ICECORE_EMIT(events ARRAY)` | SQL | One SPAN_EVENT per element, through `SYSTEM$ADD_EVENT`, named `icecore.<kind>` | `global` | Any time, for frequent small writes such as test metrics | 100 events in one CALL: **0.73 s server**, 1.71 s dbt wall. One CALL per event: **78.3 s server**, 88.2 s dbt wall (S1.md, `proc.batch`, `proc.per_event`) |
| `ICECORE_EMIT_RUN(spans ARRAY)` | Python 3.11 | Custom spans with explicit start and end times, with their events; LOG records with attributes | `icecore` | Once per run, at `on-run-end`, for the invocation and node spans | 100 spans in one CALL: **2.82 s server**, 3.93 s dbt wall; start and end kept to the millisecond for all 100 (S1.md, `proc.emit_run_python`) |

- **Records route to the event table of the procedures' database.** That is the database's own event table on Enterprise, and
  the account's table otherwise. They inherit that database's `LOG_LEVEL` and `TRACE_LEVEL` (S1.md, "Routing" and "Levels").
- **No session-level change and no `EVENTS_VIEWER` grant are needed to write.** With the session at `LOG_LEVEL = OFF` and
  `TRACE_LEVEL = OFF`, procedure records still landed, because the database's INFO / ALWAYS wins (S1.md, `proc.levels_off`).
- **The run's spans become native Snowsight traces.** All records of one `CALL` share one trace (S1.md, "Trace grouping").
- **Both procedures are persisted, not temporary**, because dbt threads use separate sessions. A temporary procedure would also
  cost a `CREATE` and a `DROP` per emission, and `CREATE PROCEDURE` at run time (S1.md, "Consequences").
- **Python packages are pinned:** `('snowflake-snowpark-python', 'opentelemetry-api==1.38.0')`.
  - An unpinned `opentelemetry-api` resolved to 1.45.1 and every procedure that imported `opentelemetry.trace` failed with
    `ImportError: cannot import name '_ExtendedAttributes'`.
  - Package lists that contained `snowflake-telemetry-python` failed with `ModuleNotFoundError: No module named
    'snowflake.snowpark'` (S1.md, "Python packages").
- **Every `CALL` is guarded.** An anonymous block with `EXCEPTION WHEN OTHER` returns `icecore-error <code>: <message>`, and the
  macro turns it into a dbt warning (S1.md, "ICECORE_EMIT (SQL)").
  - A guarded `CALL` to a missing procedure succeeds with a warning; an unguarded one fails the run-operation with `002141`
    (`proc.missing_guarded`, `proc.missing_unguarded`).
  - A missing grant returns the same error, 2141, as a missing procedure (`proc.no_usage_guarded`).
  - The guard costs about 0.1 s (`proc.batch_unguarded`: 0.63 s server against 0.73 s).
  - The guard block is anonymous, but the events belong to the procedure, so routing is unaffected (S1.md, "Routing").

## Why not option A

Option A was anonymous Snowflake Scripting blocks, plus session-level `LOG_LEVEL` / `TRACE_LEVEL`, plus reads from the shared
account table with `SNOWFLAKE.EVENTS_VIEWER`. It was rejected on S1's measurements:

- **Anonymous blocks land nothing in the database's event table.** E1 anonymous: 0/100 logs, 0/100 events, 0/1 span. E2
  anonymous Python procedure: 0/100 logs, 0/20 spans and events. Each was asserted absent for 180 s (S1.md, "Results per
  mechanism").
- That holds with session levels OFF or INFO / ALWAYS, with or without a current database, and with `USE DATABASE` inside the
  block (S1.md, routing probe `test_s1_routing_by_database_context`).
- When an anonymous block does emit, the LOG evidence puts the record in the account table as `STATEMENT`, with no database, and
  only if the session sets levels (S1.md, the lead's ACCOUNTADMIN check).
- SQL gives no `SCOPE` other than `global`, no LOG attributes and no custom spans. Only Python gives `SCOPE = icecore`, LOG
  attributes and spans with explicit times (S1.md, "Headline", point 4).

Also rejected: E3, the hybrid table. It loses records on rollback and is an owned table (S1.md, `e3_hybrid_table`). It stays
the documented last resort only.

## Identity and record shape

- `ICECORE_EMIT` records: `RECORD_TYPE = 'SPAN_EVENT'`, `SCOPE:name = 'global'`, record name prefix `icecore.`.
  SQL cannot set `SCOPE` (S1.md, "SQL record names").
- `ICECORE_EMIT_RUN` records: `SCOPE:name = 'icecore'`.
- Both: `snow.executable.type = PROCEDURE`, `snow.executable.name` starting with `ICECORE_EMIT`, `snow.database.name` =
  `<target_db>`, and `snow.query.id` = the query id of the `CALL` (S1.md, "Record shape").
- Each `CALL` is its own trace, so a dbt invocation has no common trace across CALLs: `dbt.invocation_id` goes on every record
  (S1.md, "Trace grouping").
- SPAN and SPAN_EVENT rows have a NULL `VALUE` (S1.md, "Routing"). Values, metric values included, travel as attributes.
- Spans written after the fact keep their explicit times (2 hours back in the tests, 5 days back in an exploration), so readers
  that window on `TIMESTAMP` must allow for it (S1.md, "Timestamps written after the fact").
- Every Python `CALL` also writes about 3 INFO LOG rows from the Snowpark session, with no icecore tag (S1.md, "Python
  noise", exploration). The identity filter excludes them, but they cost ingestion.

## Limits and chunking (S2)

| Limit | Measured | Source |
|---|---|---|
| One value (attribute or SQL log message) | Whole up to **960 KB** (983,040 characters); at **1 MB** the record is **silently dropped**: the CALL returns `ok` and no row lands | S2.md; `threshold.proc_event.960`, `proc_event.1024`, `sql_event.1024`, `sql_log.1024`, `python_attr.1024` |
| Python log message | Truncated to **524,252 characters plus `...`** from 512 KB up, with no flag | S2.md; `threshold.python_log.512`, `python_log.1024` |
| `ICECORE_EMIT`, events per CALL | Only the **last 128** land; the procedure's span records `dropped_events_count` (129 events: seq 2–129 kept, count 1) | S2.md; `proc_batch.129x1`, `proc_batch.1000x1` |
| `ICECORE_EMIT`, bytes per CALL | If the kept events total about **1 MB**, **none** land, with no drop count (15 × 64 KB landed, 16 × 64 KB did not) | S2.md; `proc_batch.15x64`, `proc_batch.16x64` |
| `ICECORE_EMIT_RUN`, per CALL | **200 spans** and **4 MB** (64 × 64 KB) landed whole; the 4 MB CALL took **153.81 s** of dbt wall time, against 5.30 s for 200 KB | S2.md; `emit_run_batch.200x1`, `emit_run_batch.64x64` |

The 4 MB time is client-side: Snowflake reported about 3–4 s of execution (S2.md, exploration). dbt and Jinja spend the rest
building, escaping and logging the literal.

**Rules.**

- **Chunk `ICECORE_EMIT` at most 100 events and 512 KB per CALL.** This stays under both measured limits.
- **Chunk `ICECORE_EMIT_RUN` at most 512 KB per CALL**, for dbt time rather than for a Snowflake limit.
- **Never one CALL per event.** 100 CALLs cost 78.3 s of server time against 0.73 s for one (S1.md).

## Payload cap (S2)

- **64 KB per record by default** (`payload_cap_kb`).
- **Overrides are clamped at 256 KB per record**, summed over its attributes. That leaves headroom under the 1 MB drop.
- **icecore truncates and flags** a value it cuts: Snowflake drops oversize records silently, and Python truncates log
  messages without a flag. Samples keep the spec's row-wise truncation with `dbt.sample.truncated = true`.
- **Large values go in attributes, not in log messages** (S2.md, "Payload cap and chunk size for option B").

## What setup creates and the grants it needs

`icecore.setup` creates the views and the two procedures in `<target_db>.ICECORE_OBS`, plus the alert and task objects it
already created. It creates no tables. The procedures are package-owned objects that the spec's list of allowed objects did
not include before Gate 1 (S1.md, "Consequences").

| Role | Needs | Why |
|---|---|---|
| The role that runs `setup` (owns the procedures and the views) | USAGE on `<target_db>`; CREATE SCHEMA on it, or ownership of `ICECORE_OBS`; CREATE PROCEDURE and CREATE VIEW on `ICECORE_OBS`; read access to the event table the views read | `EXECUTE AS OWNER`: the procedures write with this role's rights |
| The role that runs dbt | USAGE on `<target_db>` and `ICECORE_OBS`, and USAGE on both procedures. No session-level change, no `EVENTS_VIEWER` | S1.md, open point 2 |
| People and the app | SELECT on the views | Views are the public interface |

- A missing grant does not fail a run (the guard), but the telemetry is lost. `icecore.doctor` therefore makes a test `CALL`
  of both procedures and checks the grants (S1.md, "Consequences for building option B").
- The levels that matter are the database's. The sandbox ran with `LOG_LEVEL = INFO` and `TRACE_LEVEL = ALWAYS` on
  `ICECORE_SANDBOX` (S1.md, header). `setup` reports the effective levels and, when its role cannot set them, prints the
  statement for an admin.
- When `<target_db>` has no event table of its own, the views read the account table, through
  `SNOWFLAKE.TELEMETRY.EVENTS_VIEW` on Standard (S1.md, "Views"). The role that owns the views then needs read access to it; the
  dbt role still needs none.

## Elementary compatibility (S4): consequences for Phase 6

- `edr report` (Elementary 0.26.1) reads **12 relations**. Only some can be supplied by icecore core views, each with gaps; the
  others describe the project or hold failed rows and source freshness (S4.md, "Mapping to icecore core views").
- **Phase 6 must add a node-metadata view** (manifest metadata per node) **and a samples view** before `edr report` can render.
  Until then the compat views serve dashboards, and the report is the Streamlit app (S4.md, "Consequences for Phase 6").
- **`dbt_invocations` must expose all 35 columns of Elementary's table**, with their names and types, and `project_name` and
  `elementary_version` must carry real values. edr's first query, at the start of every command, is a `select *` on it
  (S4.md, "Capture window and environments", Phase 6 impact).
- Compat views keep Elementary's types: TEXT timestamps that Snowflake can cast, and TEXT JSON fields (S4.md, "The contract").
- `edr report` creates a temporary table in the schema it reads. Snowflake documents USAGE on the schema as enough; this is
  **unmeasured**, because the probe role owned the schema (S4.md, "What edr does besides selecting").

## Zero-scan metadata (S5): consequences for volume and freshness

- `INFORMATION_SCHEMA.TABLES.ROW_COUNT` and `SYSTEM$LAST_CHANGE_COMMIT_TIME` were correct at the first read, about 2 s after the
  write, for standard, transient and dynamic tables, and unchanged at 60 s. Volume and freshness monitors trust them with no
  delay for those kinds (S5.md, "Matrix").
- **Iceberg is unmeasured** (no external volume in the sandbox) and goes through the `COUNT` over `CHANGES` fallback until it is
  (S5.md).
- **`CHANGES` is cheap only for insert-only windows**: 4,096 bytes scanned against 35,474,944 for a full scan. After a DELETE or
  a MERGE, dbt's default incremental strategy, it scanned about 1.8 to 1.9 times a full scan. It needs change tracking, and on a
  view without it the query fails with error `091930`. So the fallback stays opt-in (S5.md, "Fallback").
- The `CHANGES` delta (`rows_added`) is a separate metric from the metadata `row_count`; never mix them in one history (S5.md).
- Freshness readers turn a token of `0` into "no change yet". On a dynamic table, freshness measures the refresh, not the
  source (S5.md, findings 2 and 3).
- Views and external tables are unsupported for zero-scan volume, with one clear message (S5.md, "Recommendation for UC2").

## Open items

1. **S3, event-table lag, has not run.** It sets the SLA default grace (2 × measured p95, spec section 10) and the anomaly
   history delay. Until then the default grace is a **10-minute placeholder** (`_sla_default_grace_minutes` in
   `macros/control/collect_sla_config.sql`). S3 also confirms which dbt commands fire `on-run-end`.
2. **SPAN and SPAN_EVENT routing to the account table is unmeasured.** Only LOG records were checked there, by the lead with
   ACCOUNTADMIN; the query's `value::string` predicate dropped every SPAN and SPAN_EVENT row (S1.md, "Routing"). Procedure
   records from a database with no event table of its own were not measured either (S1.md, open point 5).
3. **Rollback through the persisted Python procedure is unmeasured.** It was measured for `ICECORE_EMIT` (both records kept,
   `proc.rollback`) and for the temporary procedures (S1.md, open point 3).
4. **The temporary-table privilege for `edr report` is unmeasured** (S4.md, consequence 3).
5. Behaviour on dbt Fusion, dbt Cloud and `EXECUTE DBT PROJECT` is not measured (S1.md, open point 4).

## Consequences for the build (sprint 3: UC1 and UC5)

- **UC1, write path.** The writer macro chunks and `CALL`s `ICECORE_EMIT` (at most 100 events and 512 KB) for anything emitted
  during the run. `end_run` sends the invocation and node spans, with dbt's own timings, and the per-run LOG records to
  `ICECORE_EMIT_RUN` in 512 KB chunks. Both go through the guard. `dbt.invocation_id` is on every record.
- **UC5, setup and read layer.** `setup` creates the two procedures and the views in `<target_db>.ICECORE_OBS`. The views read
  the event table of `<target_db>` (`SHOW PARAMETERS LIKE 'EVENT_TABLE' IN DATABASE <target_db>`). The base event filter
  selects on `snow.executable.name` and the icecore identity: the `icecore.` name prefix for `ICECORE_EMIT` span events and
  `SCOPE:name = 'icecore'` for `ICECORE_EMIT_RUN` records. `doctor` makes a test `CALL` of both. Telemetry levels are the
  database's, not the session's.
- **Payload.** `payload_cap_kb` defaults to 64 and is clamped at 256; truncation is flagged.
