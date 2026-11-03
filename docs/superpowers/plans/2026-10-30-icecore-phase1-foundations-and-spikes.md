# icecore Phase 1: Foundations and Spikes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the icecore repo with a tested Snowflake sandbox harness, then run spikes S1–S5 so that Gate 1 can choose, from measured results, how telemetry is written to the event table.

**Architecture:** A dbt package repo (`CRITHINK-DEV/icecore`) with an `integration_tests/` dbt project that installs the package by local path. A pytest harness drives dbt through `dbtRunner` and reads the sandbox event table with the Snowflake connector. Each spike is a set of dbt macros (the probe, sent through `run_query` exactly as the package will) plus a pytest module that runs it, waits for ingestion, asserts the pass condition, and records measurements to `spikes/results/<spike>.json`. A failing spike test is a finding, not a broken build.

**Tech Stack:** dbt-core + dbt-snowflake (1.8.x floor and latest), Snowflake Scripting, Python 3.11, pytest, snowflake-connector-python, GitHub Actions.

**Spec:** `docs/design/scope-and-architecture.md` (copied into the repo in Task 1 from `Scope & Architecture — Snowflake-native dbt Observability Package.md`). Roadmap: `docs/superpowers/plans/2026-10-30-icecore-roadmap.md`.

## Why S1 is wider than the spec's row

The Snowflake docs show that Snowflake Scripting `SYSTEM$LOG` takes only a level and a message (no custom attributes); structured attributes are only available on span events (`SYSTEM$ADD_EVENT(name, {attrs})`) and on the current span (`SYSTEM$SET_SPAN_ATTRIBUTES`); custom spans and custom metrics are documented only for Python, Java, JavaScript and Scala handlers; and anonymous blocks are not mentioned at all. The spec (section 4) assumes LOG and METRIC records with `RECORD_ATTRIBUTES`, one span per node with dbt timings, and `SCOPE = 'icecore'`. S1 therefore compares three encodings and Gate 1 picks one:

| Encoding | Mechanism | What it would give |
|---|---|---|
| **E1 SQL span events** | Anonymous Snowflake Scripting block, `SYSTEM$ADD_EVENT('icecore.<kind>', :attrs)` | Structured attributes on `SPAN_EVENT` rows; no custom spans, timings carried as attributes |
| **E2 Python anonymous procedure** | `WITH p AS PROCEDURE ... LANGUAGE PYTHON ... CALL p(...)`, `logging` with `extra=`, OpenTelemetry custom spans | `LOG` rows with attributes and `SCOPE` from the logger name; custom spans; cost: Python start-up per call |
| **E3 Hybrid table** | Spec's documented fallback | Everything, at the price of owning a table |

## Global Constraints

- Package name `icecore`; dbt project name `icecore`; integration project name `icecore_integration_tests`.
- `require-dbt-version: [">=1.8.0", "<3.0.0"]`.
- Licence Apache-2.0, copyright "Crithink".
- No INSERT, no table, stage or persistent object created by package macros. Spike probes may create objects only in the sandbox schema `ICECORE_SANDBOX.SPIKES`.
- A telemetry failure must log a dbt warning and never fail the dbt run.
- S1 volume: 100 logs and 10 spans per mechanism. S2 sizes: 8 KB, 64 KB, 256 KB, 1 MB. S3: 20 concurrent `dbt build` runs, about 500 records each; pass is p95 lag under 60 s. S5: metadata correct within 60 s.
- Secrets only in environment variables or GitHub Actions secrets, never committed.

## Review Focus

1. Quotes, backslashes, newlines and non-ASCII characters in messages and attribute values (dbt error messages contain `'`): they must round-trip unchanged, never break the block. Test: Task 3 `test_s1_escaping_roundtrip`.
2. Event table missing or `LOG_LEVEL` / `TRACE_LEVEL` = `OFF`: emission must not raise. Test: Task 3 `test_s1_emission_with_levels_off_does_not_fail`.
3. Emission inside a transaction that rolls back: records must still land (spec principle 1). Test: Task 3 `test_s1_survives_rollback`.
4. Concurrent runs: no lost records and no lock waits. Test: Task 5 `test_s3_no_lost_records_under_concurrency`.
5. Quoted, mixed-case table identifiers for metadata lookups. Test: Task 7 `test_s5_quoted_identifier_table`.

---

### Task 1: Repo scaffold and offline CI

**Files:**
- Create (in `<workspace>/icecore`, a clone of `CRITHINK-DEV/icecore`): `dbt_project.yml`, `LICENSE`, `README.md`, `.gitignore`, `docs/design/scope-and-architecture.md`, `macros/.gitkeep`, `integration_tests/dbt_project.yml`, `integration_tests/packages.yml`, `integration_tests/profiles.yml`, `integration_tests/requirements-floor.txt`, `integration_tests/requirements-latest.txt`, `integration_tests/tests/test_offline.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: the dbt project `icecore` with package vars under `icecore:` (`telemetry: true`, `monitors: true`, `cost: false`, `root_cause: false`, `alerts: false`, `anomaly_engine: 'auto'`, `shadow_engine: none`, `payload_cap_kb: 64`); the integration project `icecore_integration_tests` with profile `icecore_sandbox` reading `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE` (default `ICECORE_SANDBOX`), `SNOWFLAKE_SCHEMA` (default `SPIKES`) via `env_var()`; pytest marker `sandbox` for tests that need Snowflake.

- [ ] **Step 1: Clone and copy the spec**

```bash
cd <workspace>
gh repo clone CRITHINK-DEV/icecore && cd icecore
mkdir -p docs/design && cp "../observe-event-table/Scope & Architecture — Snowflake-native dbt Observability Package.md" docs/design/scope-and-architecture.md
```

- [ ] **Step 2: Write the failing offline test** in `integration_tests/tests/test_offline.py`

```python
def test_dbt_parse_succeeds_without_a_connection():
    # dummy env values: parse must not open a connection
    assert run_dbt(["deps"]).success
    assert run_dbt(["parse"]).success

def test_package_declares_dbt_range():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))
    assert cfg["name"] == "icecore"
    assert cfg["require-dbt-version"] == [">=1.8.0", "<3.0.0"]
```

`run_dbt(args: list[str]) -> dbtRunnerResult` is a module-local helper calling `dbtRunner().invoke(args + ["--project-dir", IT_DIR, "--profiles-dir", IT_DIR])` with the `SNOWFLAKE_*` variables set to placeholders.

- [ ] **Step 3: Run it to see it fail**

Run: `cd integration_tests && pip install -r requirements-floor.txt pytest pyyaml && pytest tests/test_offline.py -v`
Expected: FAIL (no `dbt_project.yml`).

- [ ] **Step 4: Write the scaffold files** listed above. `LICENSE` is the Apache-2.0 text with `Copyright 2026 Crithink`. `integration_tests/packages.yml` installs `- local: ../`. `requirements-floor.txt`: `dbt-core~=1.8.0`, `dbt-snowflake~=1.8.0`, `snowflake-connector-python`, `pytest`, `pyyaml`. `requirements-latest.txt`: the same without pins. `.gitignore`: `target/`, `dbt_packages/`, `logs/`, `.venv/` (leave `spikes/results/` tracked: measured results are committed).

- [ ] **Step 5: Run the tests to see them pass**

Run: `pytest tests/test_offline.py -v`
Expected: 2 passed.

- [ ] **Step 6: Add `.github/workflows/ci.yml`**: job `offline` on every push and PR with a matrix `requirements: [floor, latest]` running `pytest -m "not sandbox"` in `integration_tests/`; job `sandbox` on `workflow_dispatch` only, using secrets `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY` (written to a temp file for `SNOWFLAKE_PRIVATE_KEY_PATH`), `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, running `pytest -m sandbox`.

- [ ] **Step 7: Commit and push**

```bash
git add -A && git commit -m "chore: scaffold icecore package, integration project and offline CI"
git push origin main
```
Expected: the `offline` job is green on GitHub.

---

### Task 2: Sandbox bootstrap and harness

**Files:**
- Create: `integration_tests/sandbox/bootstrap.sql`, `integration_tests/tests/conftest.py`, `integration_tests/tests/helpers/events.py`, `integration_tests/tests/helpers/findings.py`, `integration_tests/tests/test_harness.py`, `integration_tests/pytest.ini`

**Interfaces:**
- Consumes: Task 1 profile and marker.
- Produces:
  - fixture `sf()` → `snowflake.connector.SnowflakeConnection` (session scope, from the same env vars, key-pair auth).
  - fixture `run_id()` → `str` (`uuid4().hex`, function scope); every probe tags its records with it.
  - fixture `dbt()` → callable `dbt(args: list[str]) -> dbtRunnerResult` in `integration_tests/`.
  - `EVENT_TABLE: str` from env `ICECORE_EVENT_TABLE` (default `ICECORE_SANDBOX.TELEMETRY.EVENTS`).
  - `wait_for_events(sf, where_sql: str, params: dict, min_rows: int = 1, timeout_s: int = 300, poll_s: int = 5) -> list[dict]` in `helpers/events.py`: polls `SELECT * FROM {EVENT_TABLE} WHERE {where_sql}` until `min_rows` rows or timeout; returns rows as dicts with lower-case keys; raises `TimeoutError` with the count seen.
  - `record_finding(spike: str, key: str, value) -> None` in `helpers/findings.py`: merges `{key: value}` into `spikes/results/<spike>.json` (repo root), creating it if needed.

- [ ] **Step 1: Write `integration_tests/sandbox/bootstrap.sql`** for an admin to run once. Run it as `ACCOUNTADMIN`. It creates database `ICECORE_SANDBOX`, schemas `TELEMETRY` and `SPIKES`, event table `ICECORE_SANDBOX.TELEMETRY.EVENTS`, warehouse `ICECORE_CI_WH` (XSMALL, auto-suspend 60), role `ICECORE_CI` with usage on the warehouse, ownership of `SPIKES`, `SELECT` on the event table, `IMPORTED PRIVILEGES` on database `SNOWFLAKE`, and a user `ICECORE_CI_USER` with an RSA public key placeholder. It associates the event table with `ALTER DATABASE ICECORE_SANDBOX SET EVENT_TABLE = ICECORE_SANDBOX.TELEMETRY.EVENTS` (Enterprise), with the commented alternative `ALTER ACCOUNT SET EVENT_TABLE = ...` for Standard sandboxes, then sets `LOG_LEVEL = INFO` and `TRACE_LEVEL = ALWAYS` on the database.

- [ ] **Step 2: Write the failing harness tests** in `test_harness.py`

```python
@pytest.mark.sandbox
def test_event_table_is_readable(sf):
    rows = sf.cursor().execute(f"select count(*) from {EVENT_TABLE}").fetchone()
    assert isinstance(rows[0], int)

@pytest.mark.sandbox
def test_dbt_debug_connects(dbt):
    assert dbt(["debug"]).success

def test_record_finding_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(findings, "RESULTS_DIR", tmp_path)
    record_finding("s0", "a", 1); record_finding("s0", "b", 2)
    assert json.load(open(tmp_path / "s0.json")) == {"a": 1, "b": 2}
```

- [ ] **Step 3: Run them to see them fail**

Run: `pytest tests/test_harness.py -v`
Expected: FAIL (fixtures and helpers not defined).

- [ ] **Step 4: Implement `conftest.py`, `helpers/events.py`, `helpers/findings.py`, `pytest.ini`** (registers the `sandbox` marker) to the interfaces above.

- [ ] **Step 5: Run against the sandbox**

Run: `pytest tests/test_harness.py -v` (with `SNOWFLAKE_*` exported)
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "test: sandbox bootstrap and pytest harness"
```

---

### Task 3: Spike S1, write mechanisms

**Files:**
- Create: `integration_tests/macros/spikes/s1_sql.sql`, `integration_tests/macros/spikes/s1_python.sql`, `integration_tests/tests/spikes/test_s1_write_mechanisms.py`, `spikes/S1.md`

**Interfaces:**
- Consumes: `sf`, `dbt`, `run_id`, `wait_for_events`, `record_finding`.
- Produces (macros in `icecore_integration_tests`, each executing one anonymous statement through `run_query`):
  - `s1_sql_log(run_id: str, n: int, message: str = 'probe')`: block with `n` × `SYSTEM$LOG_INFO('<message> ' || run_id || ' ' || i)`.
  - `s1_sql_event(run_id: str, n: int, attrs: dict)`: block that builds `LET a OBJECT := PARSE_JSON(<attrs + run_id as JSON literal>)` and calls `SYSTEM$ADD_EVENT('icecore.probe', :a)` `n` times.
  - `s1_sql_span_attrs(run_id: str, attrs: dict)`: block calling `SYSTEM$SET_SPAN_ATTRIBUTES` with the same object.
  - `s1_python(run_id: str, n_logs: int, n_spans: int, attrs: dict)`: `WITH probe AS PROCEDURE(...) RETURNS STRING LANGUAGE PYTHON RUNTIME_VERSION = '3.11' PACKAGES = ('snowflake-snowpark-python', 'snowflake-telemetry-python', 'opentelemetry-api') HANDLER = 'run' AS $$ ... $$ CALL probe(...)`. The handler logs `n_logs` lines through `logging.getLogger('icecore')` with `extra=attrs | {'icecore.run_id': run_id}`, and opens `n_spans` custom spans named `icecore.node` via `opentelemetry.trace.get_tracer('icecore')`, each with one `add_event`.
  - `s1_in_rollback(run_id: str)`: `BEGIN TRANSACTION`, `s1_sql_event` and `s1_sql_log` for 1 record each, then `ROLLBACK`.
  - All literals go through one helper macro `s1_sql_literal(value: str) -> str` that escapes `\` and `'` for a single-quoted Snowflake string.

- [ ] **Step 1: Write the failing probe tests** (all `@pytest.mark.sandbox`; each records `elapsed_s` and what it saw with `record_finding("s1", ...)`):

```python
ATTRS = {"dbt.node.unique_id": "model.p.fct_orders", "dbt.result.rows_affected": 1200,
         "dbt.result.execution_time_s": 4.2, "dbt.test.failures": 0, "icecore.flag": True}

def test_s1_sql_log_lands(dbt, sf, run_id):          # 100 LOG rows, RESOURCE_ATTRIBUTES has snow.query.id
def test_s1_sql_event_carries_attributes(...):       # 100 SPAN_EVENT rows; RECORD_ATTRIBUTES == ATTRS + run_id, types preserved
def test_s1_sql_span_attrs_land(...):                # one SPAN row whose RECORD_ATTRIBUTES include ATTRS
def test_s1_python_logs_carry_attributes_and_scope(...):  # 100 LOG rows, RECORD_ATTRIBUTES ⊇ ATTRS, SCOPE:name = 'icecore'
def test_s1_python_custom_spans(...):                # 10 SPAN rows named icecore.node, each with 1 SPAN_EVENT
def test_s1_trace_grouping(...):                     # two consecutive run-operations: record whether TRACE:trace_id is shared (finding, asserts it is recorded)
def test_s1_escaping_roundtrip(...):                 # message and attr value "it's a \\ test\nnew line é ü 漢" arrive byte-identical, for E1 and E2
def test_s1_emission_with_levels_off_does_not_fail(...):  # ALTER SESSION SET LOG_LEVEL = OFF, TRACE_LEVEL = OFF; each macro returns success, 0 rows land
def test_s1_survives_rollback(...):                  # s1_in_rollback: the event and the log still land
```

Assertions read rows with `wait_for_events(sf, "RECORD_ATTRIBUTES:\"icecore.run_id\" = %(r)s OR VALUE::string ILIKE %(p)s", {"r": run_id, "p": f"%{run_id}%"}, min_rows=...)`.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/spikes/test_s1_write_mechanisms.py -v`
Expected: FAIL (macros not defined).

- [ ] **Step 3: Implement the macros** to the interfaces above.

- [ ] **Step 4: Run the probes against the sandbox**

Run: `pytest tests/spikes/test_s1_write_mechanisms.py -v`
Expected: every test either passes or fails with a recorded finding; `spikes/results/s1.json` holds `elapsed_s` per mechanism, the observed `RECORD_TYPE`, `SCOPE`, `RESOURCE_ATTRIBUTES` keys and trace grouping.

- [ ] **Step 5: Write `spikes/S1.md`**: the SQL used, a results table (mechanism × lands / attributes / scope / spans / escaping / levels-off / rollback / seconds per 100 records), and a recommendation among E1, E2 and E3.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "spike(S1): compare SQL span events, Python procedure and fallback write paths"
```

---

### Task 4: Spike S2, payload limits

**Files:**
- Create: `integration_tests/macros/spikes/s2_payload.sql`, `integration_tests/tests/spikes/test_s2_payload.py`, `spikes/S2.md`

**Interfaces:**
- Consumes: Task 3 macros `s1_sql_log`, `s1_sql_event`, `s1_python` (reused with a `message` / attribute of the target size).
- Produces: macro `s2_payload(run_id: str, mechanism: str, size_kb: int)` with `mechanism` in `('sql_log', 'sql_event', 'python_log')`, emitting one record whose message (logs) or attribute `icecore.payload` (events) is `size_kb` × 1024 ASCII characters.

- [ ] **Step 1: Write the failing test**, parametrized over mechanism × size `[8, 64, 256, 1024]`:

```python
@pytest.mark.sandbox
@pytest.mark.parametrize("mechanism", ["sql_log", "sql_event", "python_log"])
@pytest.mark.parametrize("size_kb", [8, 64, 256, 1024])
def test_s2_payload(dbt, sf, run_id, mechanism, size_kb):
    res = dbt(["run-operation", "s2_payload", "--args", json.dumps({...})])
    # finding: did the call succeed, did a row land, length of what landed
    record_finding("s2", f"{mechanism}.{size_kb}", {"call_ok": res.success, "landed_len": landed_len})
    assert res.success  # a hook must never fail on size
```

- [ ] **Step 2: Run to see it fail** (`pytest tests/spikes/test_s2_payload.py -v`, macro missing).
- [ ] **Step 3: Implement `s2_payload`.**
- [ ] **Step 4: Run against the sandbox**; expected: results for all 12 cases in `spikes/results/s2.json`.
- [ ] **Step 5: Write `spikes/S2.md`** with the limit per mechanism, what happens above it (error, truncation or silent drop), and the payload cap to use (spec default 64 KB).
- [ ] **Step 6: Commit** `spike(S2): measure payload limits per mechanism`.

---

### Task 5: Spike S3, ingestion lag, concurrency and hook coverage

**Files:**
- Create: `integration_tests/macros/spikes/s3_hooks.sql`, `integration_tests/models/spikes/s3_model.sql`, `integration_tests/models/spikes/schema.yml`, `integration_tests/seeds/s3_seed.csv`, `integration_tests/snapshots/s3_snapshot.sql`, `integration_tests/tests/spikes/test_s3_lag_and_hooks.py`, `spikes/S3.md`
- Modify: `integration_tests/dbt_project.yml` (add `on-run-start` / `on-run-end` calling `s3_hook_probe('start')` / `s3_hook_probe('end')` only when `var('s3_probe', false)`; a source with freshness on `s3_seed`)

**Interfaces:**
- Consumes: the mechanism `spikes/S1.md` recommends: `s1_sql_event` for E1, `s1_python` for E2 (for E3, stop and plan the hybrid-table write first).
- Hooks in `integration_tests/dbt_project.yml`: `on-run-start: "{{ s3_hook_probe('start') if var('s3_probe', false) else '' }}"`, same for `on-run-end` with `'end'`.
- Produces: macro `s3_hook_probe(phase: str)` emitting one record with attributes `icecore.run_id = var('run_id')`, `icecore.phase`, `dbt.command = flags.WHICH`, `icecore.emitted_at = current UTC ISO timestamp`; macro `s3_burst(run_id: str, n: int)` emitting `n` records in one call.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.sandbox
@pytest.mark.parametrize("command", [["build"], ["run"], ["test"], ["seed"], ["snapshot"], ["source", "freshness"]])
def test_s3_end_hook_fires(dbt, sf, run_id, command):
    dbt(command + ["--vars", json.dumps({"s3_probe": True, "run_id": run_id})])  # the project holds only spike resources in Phase 1
    rows = wait_for_events(sf, ..., min_rows=2)   # start + end
    assert {r["phase"] for r in rows} == {"start", "end"}

@pytest.mark.sandbox
def test_s3_no_lost_records_under_concurrency(sf, run_id):
    # 20 threads, each its own dbt invocation of s3_burst(run_id, 500)
    rows = wait_for_events(sf, ..., min_rows=10_000, timeout_s=900)
    assert len(rows) == 10_000

@pytest.mark.sandbox
def test_s3_lag_p95_under_60s(sf, run_id):
    # lag = first-seen time (poll every 2 s) - icecore.emitted_at, per record; record p50 / p95
    assert p95 < 60
```

- [ ] **Step 2: Run to see them fail** (macros and models missing).
- [ ] **Step 3: Implement** the hooks, burst macro, minimal model, seed, snapshot and source freshness config.
- [ ] **Step 4: Run against the sandbox** on `requirements-floor.txt`, then again on `requirements-latest.txt`; if the `dbtf` (Fusion) binary is installed, run `test_s3_end_hook_fires` with it too. Expected: `spikes/results/s3.json` has hook coverage per command and dbt version, p50/p95 lag, and the record count.
- [ ] **Step 5: Write `spikes/S3.md`**: hook coverage table, lag numbers, the default SLA grace (2 × measured p95) and history delay to use.
- [ ] **Step 6: Commit** `spike(S3): ingestion lag, concurrency and hook coverage`.

---

### Task 6: Spike S4, the `edr report` contract

S4 cannot pass before compatibility views exist (Phase 6). Phase 1 captures what `edr report` reads, so Phase 6 has a fixed target.

**Files:**
- Create: `integration_tests/elementary_probe/dbt_project.yml`, `integration_tests/elementary_probe/packages.yml` (`elementary-data/elementary`, latest), `integration_tests/elementary_probe/models/` (two models, one Elementary test), `integration_tests/tests/spikes/test_s4_edr_contract.py`, `spikes/S4.md`, `spikes/results/s4_edr_queries.sql`
- Install: `pip install 'elementary-data[snowflake]'` in the test environment (not in the requirements files; S4 only)

**Interfaces:**
- Produces: `spikes/results/s4.json` with `{relation_name: [column, ...]}` for every Elementary relation `edr report` selects from.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.sandbox
def test_s4_capture_edr_contract(sf, tmp_path):
    # in elementary_probe/: dbt deps, dbt run --select elementary, dbt build, edr report --file-path tmp_path/report.html
    # then read QUERY_HISTORY_BY_SESSION for the edr session and parse FROM / column lists
    contract = capture_edr_contract(sf, session_id)
    record_finding("s4", "contract", contract)
    assert "dbt_invocations" in contract and "elementary_test_results" in contract
```

- [ ] **Step 2: Run to see it fail** (probe project missing).
- [ ] **Step 3: Implement** the `elementary_probe/` project and `capture_edr_contract(sf, session_id: int) -> dict[str, list[str]]` in the test module, reading `TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION(SESSION_ID => ...))`.
- [ ] **Step 4: Run against the sandbox**; expected: `s4.json` lists the relations and columns `edr report` reads, and `s4_edr_queries.sql` holds the raw queries.
- [ ] **Step 5: Write `spikes/S4.md`**: the contract, the Elementary version it was captured from, and which icecore core views can supply each relation.
- [ ] **Step 6: Commit** `spike(S4): capture the edr report contract`.

---

### Task 7: Spike S5, zero-scan metadata reliability

**Files:**
- Create: `integration_tests/macros/spikes/s5_metadata.sql`, `integration_tests/tests/spikes/test_s5_metadata.py`, `spikes/S5.md`

**Interfaces:**
- Produces: macro `s5_metadata(relation: str) -> dict` returning `{"row_count": int|None, "last_change": str|None}` from `INFORMATION_SCHEMA.TABLES.ROW_COUNT` and `SYSTEM$LAST_CHANGE_COMMIT_TIME(relation)`; macro `s5_write(relation: str, n: int)` inserting `n` rows (spike-only, in `SPIKES`).

- [ ] **Step 1: Write the failing tests**, parametrized over table kind `["standard", "transient", "dynamic", "iceberg"]` (iceberg skipped with a recorded reason when env `ICECORE_EXTERNAL_VOLUME` is unset):

```python
@pytest.mark.sandbox
def test_s5_row_count_and_change_time(dbt, sf, kind):
    # create, write 100 rows, read metadata at t=0 and t=60s
    record_finding("s5", kind, {"t0": m0, "t60": m60})
    assert m60["row_count"] == 100 and m60["last_change"] is not None

def test_s5_quoted_identifier_table(dbt, sf):
    # table "Mixed Case ""Orders""" in SPIKES: metadata lookup finds it and returns 100
```

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement** the two macros; `relation` is always a fully quoted three-part name built with `adapter.quote`.
- [ ] **Step 4: Run against the sandbox**; expected: a per-kind matrix in `spikes/results/s5.json`.
- [ ] **Step 5: Write `spikes/S5.md`**: the per-kind matrix and the fallback to `COUNT(*)` over `CHANGES` for each kind that fails.
- [ ] **Step 6: Commit** `spike(S5): metadata reliability per table kind`.

---

### Task 8: Gate 1 decision record

**Files:**
- Create: `spikes/GATE1.md`
- Modify: `docs/design/scope-and-architecture.md` (sections 3, 4, 8 and 10)

- [ ] **Step 1: Write `spikes/GATE1.md`**: the chosen encoding (E1, E2 or E3) with the S1 evidence; payload cap (S2); lag, grace and history delay (S3); the `edr` contract (S4); the per-kind metadata fallback (S5); and the consequences for Phase 2.
- [ ] **Step 2: Update the spec**: section 4 record types and attribute placement to match the chosen encoding (for E1: `SPAN_EVENT` rows named `icecore.<kind>`, timings as attributes, view filter on event name instead of `SCOPE` if S1 showed `SCOPE` cannot be set from SQL); section 8 rows S1–S5 with measured results; section 10 risks closed or re-rated.
- [ ] **Step 3: Verify**: every S1–S5 row in section 8 cites its `spikes/S<n>.md`; `pytest -m "not sandbox"` still passes.
- [ ] **Step 4: Commit and tag**

```bash
git add -A && git commit -m "docs: gate 1 decision record and spec update from spike results"
git tag gate-1 && git push origin main --tags
```
