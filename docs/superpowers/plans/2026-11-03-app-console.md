# icecore app: the Console — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the icecore Streamlit app as the Console. Every page follows one anatomy: title, KPI tiles, filters, one chart, one table, failures first, a row click opening the node. It runs locally and in Snowflake.

**Architecture:**
- **Pure logic** lives in `streamlit/lib/` modules with offline tests: status mapping, health score, incident state, chart specs.
- **Shared page anatomy**: `streamlit/lib/console.py` is the only module that touches Streamlit widgets for the anatomy.
- **Pages** are thin files under `streamlit/pages/`.
- **Reads** go through the existing `lib/queries.py`.
- **Lineage** is the one custom component (React Flow, bundled). A Graphviz fallback covers runtimes that cannot load it.

**Tech Stack:** Streamlit 1.50.0, Altair, pandas, snowflake-snowpark-python. The lineage component uses React 18, reactflow 11 and dagre, built with Vite.

**Spec:** [docs/superpowers/specs/2026-11-03-app-console-design.md](../specs/2026-11-03-app-console-design.md). Prototype: [docs/design/app-prototype/console.html](../../design/app-prototype/console.html).

## Global Constraints

**Runtime and packages**
- Streamlit **1.50.0**; packages only from the Snowflake Anaconda channel (`streamlit/environment.yml`). Charts are **Altair**; no streamlit-echarts.

**Reading data**
- Every read goes through `streamlit/lib/queries.py` (cached, bound parameters, `since` windows, `MAX_LIMIT`). Nothing reads the event table.
- Every read is guarded: on an exception the page shows its empty state with the error text in a caption. No crash, no silent empty.

**Safety**
- No value is ever built into SQL text. The only write is `lib/ack.py` (a bound `CALL ICECORE_ACK(?)`).
- Any data rendered through `st.html` or `st.markdown` is escaped (`lib.console.esc` / `md`).

**Status colours and labels**

| Status | Colour | Label |
|---|---|---|
| failed | red `#c62828` | 🔴 |
| warning | amber `#b26a00` | 🟠 |
| ok | green `#2e7d32` | 🟢 |
| late or stale | violet `#6a4fb3` | 🟣 |
| not run | grey `#9e9e9e` | ⚪ |

**Look**
- Light and dark themes in `streamlit/.streamlit/config.toml`, and no other CSS.

**Health score**
- Weights: tests 0.5, freshness 0.2, schedules 0.15, anomalies 0.15, renormalised over the components that apply.

**Incident state**
- Precedence: resolved > acknowledged > open. An open incident first seen more than 7 days ago is "stale" and is not counted in titles or tiles.

**Missing data**
- Data a view does not have yet (layer, freshness threshold, notifications, tested node) shows "not recorded yet" and its control is hidden. Nothing is guessed.

**Testing**
- Streamlit-dependent tests run in the Streamlit 1.50 venv: from `integration_tests`, `$SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_*.py tests/test_app_shell.py`.
- The full offline suite still runs on both dbt venvs.

**Prerequisites:** PR #99 (local mode) merged into develop. Stories ab08f982 (layer, threshold), ee1ee2c5 (notifications) and 94f5b653 (tested node) may land during the build; no task blocks on them.

## Review Focus

1. **Fresh install or empty window.** Every page shows its empty state. Tiles read "0" or "—", never an exception. Pinned in Task 1 (`test_each_page_renders_an_empty_state`) and by an all-empty case in every page task.
2. **Older views without the new columns** (`layer`, `threshold`, `tested_unique_id`, the `NOTIFICATIONS` view). Logic gets rows missing those keys and degrades to "not recorded yet". Pinned in Tasks 2, 4, 8 and 12 (`test_*_without_new_columns`).
3. **Big projects past `MAX_LIMIT`.** A table built from a read that hit the limit says so ("showing the latest 50,000"), and the lineage keeps working at 2,000 nodes. Pinned in Task 1 (`test_truncated_flag`) and Task 14 (`test_lineage_payload_2000_nodes`).
4. **Names with markup** (`<script>`, `*bold*`, `_x_`, `|`) in model, test and incident names are shown as text everywhere. Pinned in Task 1 (`test_esc_and_md_escape_markup`) and Task 5 (an incident titled `<b>x</b>`).
5. **Clock skew** between dbt's clock and Snowflake's: no negative ages, and "as of" comes from the newest record, not the app's clock. Pinned in Task 2 (`test_incident_age_never_negative`, `test_as_of_is_latest_record`).

---

### Task 1: Foundations: theme, status mapping, page anatomy, navigation

**Files:**
- Create: `streamlit/.streamlit/config.toml`, `streamlit/lib/status.py`, `streamlit/lib/console.py`, and `streamlit/lib/pages/__init__.py` (the package for each page's pure logic, used by Tasks 4 to 14)
- Create stubs (title and empty state only): `streamlit/pages/overview.py`, `incidents.py`, `schedules.py`, `schema_changes.py`, `node.py`
- Modify: `streamlit/lib/navigation.py`, `streamlit/streamlit_app.py`, `streamlit/lib/ui.py` (keep `empty_state`; `page_header` becomes a thin alias of `console.page`)
- Delete: `streamlit/pages/slas.py` (its logic in `lib/slas.py` stays; the Schedules page uses it)
- Test: `integration_tests/tests/test_app_console.py`; update `integration_tests/tests/test_app_shell.py` (`EXPECTED_PAGES`)

**Interfaces:**
- Produces, in `lib/status.py` (pure, no Streamlit):
  - `STATUSES = ("failed", "warning", "ok", "late", "not_run")`
  - `COLOURS: dict[str, str]`, the hex values above
  - `normalise(raw: str | None) -> str`: success, pass → ok; error, fail, runtime error → failed; warn → warning; skipped, None → not_run; late, stale, silent → late
  - `label(status: str) -> str`, for example `"🔴 Failed"`
  - `sort_key(status: str) -> int`, failed first
- Produces, in `lib/console.py`:
  - `esc(s) -> str` (HTML) and `md(s) -> str` (escapes markdown specials)
  - `page(title: str, question: str, views: list[str]) -> None`
  - `@dataclass Tile(label: str, value, delta: str | None = None, help: str = "", series: list[float] | None = None, chart: str = "line")` and `kpi_row(tiles: list[Tile]) -> None`
  - `guarded(read: Callable[[], list[dict]]) -> tuple[list[dict], str | None]`
  - `empty(message: str, error: str | None = None) -> None`
  - `@dataclass Filters(days: int, layer: str | None, statuses: list[str])` and `filter_bar(key: str, *, layers: list[str] | None, statuses: bool = True) -> Filters`. The window is 7 or 15 days, default 15. `layers=None` hides the layer control. Values are mirrored in `st.query_params` keys `days`, `layer`, `status`.
  - `table(rows: list[dict], column_config: dict, key: str, *, limit: int | None = None, node_column: str | None = "unique_id") -> dict | None`. It sorts failures first, shows a "showing the latest N" caption when `len(rows) == limit`, and on single-row selection calls `open_node`.
  - `open_node(unique_id: str) -> None`: sets `st.query_params["node"]` and calls `st.switch_page("pages/node.py")`
- Produces, in `lib/navigation.py`: `PAGES` in the spec's order, each `{"path", "title", "icon", "group"}` with group main, explore or bottom:
  - main: Overview, Incidents, Tests & anomalies, Runs, Schedules & freshness, Schema changes, Cost
  - explore: Lineage, Models & sources
  - bottom: Settings & about

- [ ] **Step 1: Write the failing tests** in `test_app_console.py`:
  - `test_normalise_maps_every_dbt_status`: a table of input → output for every status listed above.
  - `test_label_and_sort_failures_first`: `sorted(["ok", "failed", "warning", "not_run", "late"], key=sort_key) == ["failed", "warning", "late", "ok", "not_run"]`.
  - `test_esc_and_md_escape_markup`: `esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"`, and `md("*a*_b_|c")` contains no unescaped `*`, `_` or `|`.
  - `test_truncated_flag`: a pure helper `console.truncation_note(rows, limit)` returns `"Showing the latest 3 rows."` when `len(rows) == limit == 3`, else `None`.
  - In `test_app_shell.py`: `EXPECTED_PAGES` lists the ten pages in order. Keep `test_each_page_renders_an_empty_state` and the views-schema tests.
- [ ] **Step 2: Run** `$SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_console.py tests/test_app_shell.py`. Expected: FAIL (module `lib.status` missing; page list differs).
- [ ] **Step 3: Implement** `status.py`, `console.py`, `config.toml` and `navigation.py`. In `streamlit_app.py`, use `st.navigation` with sections `{"": main, "Explore": explore, " ": bottom}`, plus the global node search (a `st.selectbox` over `lineage_latest` node names, which calls `open_node`). The search hides itself when the read fails.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then run the full offline suite on `.venv` and `.venv-latest` from a clean `target/`, `dbt_packages/` and `package-lock.yml`. Expected: no new failures.
- [ ] **Step 5: Commit** `feat(app): console foundations: theme, status, page anatomy, navigation`.

### Task 2: Domain logic: health score, incident state, "as of"

**Files:**
- Create: `streamlit/lib/health.py`, `streamlit/lib/incidents.py`
- Test: `integration_tests/tests/test_app_domain.py`

**Interfaces:**
- Consumes: `status.normalise`.
- Produces, in `health.py`:
  - `WEIGHTS = {"tests": 0.5, "freshness": 0.2, "schedules": 0.15, "anomalies": 0.15}`
  - `components(test_statuses: list[str], sources_within_limit: list[bool], schedules_on_time: list[bool], metrics_anomalous: list[bool]) -> dict[str, float | None]`. An empty list gives `None` (n/a). tests = mean of pass 100, warn 50, fail or error 0.
  - `score(components: dict[str, float | None]) -> float | None`: weighted mean renormalised over the non-None components; None when all are None.
  - `formula_help() -> str`: the markdown for the tile `help`.
- Produces, in `incidents.py`:
  - `as_of(*row_lists: list[dict], keys=("event_at", "detected_at", "last_detected_at", "started_at", "executed_at", "observed_at", "recorded_at")) -> datetime | None`: the newest timestamp found.
  - `state(incident: dict, *, test_results: list[dict], node_executions: list[dict], metrics: list[dict], now: datetime) -> str`, returning `resolved`, `acknowledged`, `open` or `stale`, with these rules from spec ruling 4:
    - **test_failure:** resolved by a later `pass` of `node_key`.
    - **silence:** resolved by a node success with `started_at` after `first_detected_at`.
    - **anomaly and cost:** resolved when the next metric point of the same `relation`/`metric`/`column`/`dimension`, after `last_detected_at`, has `anomaly_is_anomaly` false.
    - **schema_change:** resolved when acknowledged.
    - **stale:** open, and `first_detected_at` is older than 7 days before `now`.
  - `age(ts: datetime, now: datetime) -> timedelta`: never negative (clamped at zero).
  - `summary(states: list[tuple[dict, str]]) -> dict`: keys `open`, `acknowledged`, `errors_open`, `oldest_open`, `stale`.

- [ ] **Step 1: Write the failing tests**:
  - `test_score_weights_and_renormalisation`: `score({"tests": 100, "freshness": None, "schedules": 0, "anomalies": None}) == round(100 * 0.5 / 0.65, 1)`, and all-None gives None.
  - `test_components_from_statuses`: `["pass", "warn", "fail"]` gives tests 50.0.
  - `test_state_per_kind`: one case per kind, plus acknowledged and stale.
  - `test_incident_age_never_negative`: `age(now + 5min, now) == timedelta(0)`.
  - `test_as_of_is_latest_record`.
  - `test_state_without_new_columns`: rows missing `root_cause_kind` or `tested_unique_id` do not raise.
- [ ] **Step 2: Run** `$SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_domain.py`. Expected: FAIL on import.
- [ ] **Step 3: Implement** `health.py` and `incidents.py`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(app): health score and incident state`.

### Task 3: Chart library

**Files:**
- Modify: `streamlit/lib/charts.py` (keep `timeline_bars` and `timeline_chart`; add below them)
- Test: `integration_tests/tests/test_app_charts.py`

**Interfaces:**
- Consumes: `status.COLOURS`.
- Produces. All charts are titled, both axes titled with units, colours from `COLOURS`, and the timezone is UTC, as stored:
  - `band_chart(points: list[dict], *, title: str, unit: str, engine: str | None = None) -> alt.LayerChart`. The points are METRICS rows. It layers an area from `lower` to `upper`, a dashed `expected` line, a `value` line, and red points where `anomaly_is_anomaly` is true.
  - `runs_chart(runs: list[dict]) -> alt.LayerChart`: duration bars, coloured by normalised status, with failing tests on a right axis.
  - `gantt(nodes: list[dict]) -> alt.Chart`: `mark_bar` with x = `started_at`, x2 = start + `execution_time_s`, y = node name sorted by start, coloured by status.
  - `daily(rows: list[dict], *, x: str, y: str, title: str, y_title: str, color: str | None = None) -> alt.Chart`.
  - `lag_vs_limit(sources: list[dict]) -> alt.LayerChart`: bars of lag, with a tick at `threshold` when present.

- [ ] **Step 1: Write the failing tests**. Each builds its chart from three or four rows and asserts on `chart.to_dict()`:
  - the title text;
  - the axis titles;
  - the number of layers (4 for the band chart);
  - the anomaly layer's filter.

  Plus `test_charts_accept_empty_rows`: every builder returns a chart with no data, without raising.
- [ ] **Step 2: Run** `$SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_charts.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the builders in `charts.py`.
- [ ] **Step 4: Run**. Expected: PASS.
- [ ] **Step 5: Commit** `feat(app): chart library`.

> **Tasks 4 to 12 are the pages.** They depend on Tasks 1 to 3, touch separate files, and can run in parallel.
>
> Each page task follows the same steps:
> 1. Write a failing logic test file `integration_tests/tests/test_app_page_<name>.py` for `lib/pages/<name>.py`. That module holds pure functions, rows in and tile, table and chart data out.
> 2. Watch it fail.
> 3. Implement the logic and the thin page `streamlit/pages/<name>.py` with the anatomy, the tiles and the columns listed in the spec's Sections table.
> 4. Run the logic tests, `test_app_shell.py`, and an AppTest render of the page with fixture rows (the reads are patched).
> 5. Commit `feat(app): <page> page`.
>
> Every page test file includes `test_<name>_all_empty` (all reads return `[]`) and `test_<name>_read_error_shows_caption` (a read raises).

### Task 4: Overview
`lib/pages/overview.py`:
- `tiles(incidents, runs, test_results, slas, silences, credits_rows, *, now) -> list[Tile]`: open incidents, failing tests, successful runs, missed schedules, credits.
- `needs_attention(incidents_with_state) -> list[dict]`: open ones, errors first, then by first seen.
- `layer_health(rows_by_layer) -> list[dict] | None`: None when the rows have no `layer` key, which hides the strip.
- Chart: `charts.runs_chart`. Second table: nodes that are not OK.
- Tests include `test_overview_without_new_columns`: no `layer` hides the health strip.

### Task 5: Incidents
`lib/pages/incidents.py`:
- `rows(incidents, states) -> dict[str, list[dict]]`, grouped open (stale listed last, with a 🕓 marker), acknowledged, resolved.
- `tiles(...)`.
- Chart: incidents per day by kind (`charts.daily`).
- Acknowledge: a `st.button` on the selected open row calls `lib/ack.acknowledge(incident_key)`. The result goes to `st.toast`, then `queries.clear_cache()` and `st.rerun()`. If `lib/ack.py` is not on develop yet, the button is hidden with "acknowledgement needs story 23601af8".
- Tests include an incident titled `<b>x</b>`, rendered as text in the table.

### Task 6: Tests & anomalies
`lib/pages/tests.py`:
- `results_table(test_results, metrics) -> list[dict]`: failures first, with `engine` (native / Cortex / both).
- `series_for(metrics, relation, metric, column=None, dimension=None) -> dict[str, list[dict]]`, keyed by engine. When both engines scored, show two band charts side by side, with their scores in captions.
- Tiles: failing, warning, anomalies, monitors scored by Cortex.

### Task 7: Runs
Reuses `lib/runs_logic.py`. Logic: `tiles`, `runs_table`, `slowest_nodes(node_rows, n=10)`.
- Chart: `runs_chart`; Gantt of the selected run (`charts.gantt`).
- Credits show "pending" while `credits` is null.
- The selected run lives in `st.query_params["run"]`.

### Task 8: Schedules & freshness
Reuses `lib/slas.py`. Logic: `tiles`, `schedules_table(sla_declared, silences, node_rows)`, `sources_table(metrics)`.
- The sources table reads the latest `freshness_lag_seconds` per relation, with `threshold` when present; otherwise "no limit set".
- Chart: `charts.lag_vs_limit`.
- `test_schedules_without_new_columns`: no `threshold` key means no limit tick, and "sources over limit" reads "—".

### Task 9: Schema changes
Logic: `tiles`, `changes_table(schema_changes)`, `per_day(schema_changes)` stacked by `change_kind`.
- The node page shows the column snapshot from `SCHEMA_COLUMNS`. Add `build_schema_columns` / `schema_columns(relation, limit)` at the end of `queries.py`, following the pattern of `build_silence_events`, and add the view to `VIEWS`.

### Task 10: Cost
Reuses `lib/cost.py`. Logic:
- `tiles`: credits in window, change against the previous window, top model share, unattributed runs.
- `per_model(node_rows)` with a 15-point trend list for `LineChartColumn`.
- Chart: daily credits (`charts.daily`). Second table: top regressions.

### Task 11: Models & sources (node page)
Logic: `node_summary(unique_id, node_rows, test_results, metrics, schema_changes, lineage_rows) -> dict`, covering status history, tests, metrics series, schema history and cost.
- The page reads `st.query_params["node"]`; without it, it shows a node picker.
- The lineage neighbourhood uses the Task 14 component when available, else Task 14's `lineage_fallback` (one hop up and down).
- `test_node_unknown_id`: "No node with this id" with the picker. No exception.

### Task 12: Settings & about
Reuses the data layer in draft PR #101 (`lib/config_logic.py`); cherry-pick it. Logic:
- capability rows (edition, event table, Cortex, levels, setup version);
- versions per run, with "not recorded yet" when `icecore_version` is missing;
- channels with their last notification, "not recorded yet" until the `NOTIFICATIONS` view exists.

`test_settings_without_new_columns`.

### Task 13: Spike: can Streamlit in Snowflake load a bundled custom component?

**Files:** create `spikes/S14-sis-component.md` and `spikes/results/s14.json`, plus a throwaway minimal component under `spikes/s14_component/` (an `index.html` and one JS file that calls `Streamlit.setComponentValue`).

- [ ] **Step 1:** Deploy a two-file Streamlit app in the sandbox (schema `ICECORE_SANDBOX.SPIKES`, object name with a run_id) that declares the component with `components.declare_component(path=...)`. Use the existing `streamlit/snowflake.yml` pattern and the warehouse runtime. Then open it.
- [ ] **Step 2:** Record in `s14.json`:
  - does it render?
  - does a click reach Python?
  - which runtime;
  - the Streamlit version;
  - any CSP or console error.
- [ ] **Step 3:** Write S14.md with one decision line: "React Flow in Snowflake: yes/no", plus the fallback that applies.
- [ ] **Step 4:** Drop the sandbox objects. Commit `spike(s14): custom component in Streamlit in Snowflake`.

### Task 14: Lineage component and page

**Files:**
- Create: `streamlit/components/lineage/frontend/` (Vite + React + reactflow 11 + dagre source, `package.json` with exact versions) and `streamlit/components/lineage/build/` (the committed build output).
- Create: `streamlit/lib/lineage_component.py`, `streamlit/lib/pages/lineage.py`; rewrite `streamlit/pages/lineage.py`.
- Test: `integration_tests/tests/test_app_lineage.py`

**Interfaces:**
- Consumes: `lib/dag.py` (statuses, stale marking), `status.COLOURS`, `console.open_node`.
- Produces:
  - `payload(lineage_rows, node_rows, test_results, *, highlight: str | None, root_cause: str | None) -> dict`, with `{"nodes": [...], "edges": [...], "highlight": ..., "rootCausePath": [...]}`. Each node carries `id`, `label`, `type`, `layer`, `status`, `tests_failing`.
  - `lineage(payload: dict, *, height: int = 640, key: str) -> str | None`: the component, which returns the clicked node id.
  - `lineage_fallback(payload: dict) -> None`: `st.graphviz_chart` with the same colours and a bold root-cause path.
  - `use_component() -> bool`: true locally; in Snowflake, Task 13's answer.
- Component behaviour (from the spec):
  - pan, zoom, minimap;
  - status overlay;
  - a node click highlights its upstream and downstream and sends the id back;
  - the root-cause path drawn in red;
  - a layer filter (hidden without a `layer`);
  - search;
  - a left-to-right dagre layout;
  - light and dark themes from Streamlit's theme.

- [ ] **Step 1: Write the failing tests**:
  - `test_payload_statuses_and_root_cause_path`: fixture rows; the path is the ordered list from root cause to test.
  - `test_payload_without_layer`.
  - `test_lineage_payload_2000_nodes`: builds in under 1 s and stays under 2 MB as JSON.
  - `test_fallback_dot_escapes_names`: a node named `a"b<c` is quoted in the DOT text.
- [ ] **Step 2: Run** `$SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_lineage.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `payload`, `lineage_fallback`, `use_component`, the React component (`npm ci && npm run build` in `frontend/`; commit `build/`) and the page. The page's right pane shows the clicked node's summary and an "Open node page" button that calls `console.open_node`.
- [ ] **Step 4: Run** the tests, `test_app_shell.py`, and a local manual check with `make app` on the demo data. Click a failed test and confirm the red path appears.
- [ ] **Step 5: Commit** `feat(app): interactive lineage`.

### Task 15: End-to-end check, docs, Snowflake deploy

**Files:**
- Modify: `docs/app.md` (pages, local first, deploy), `README.md` (app section, one screenshot), `CHANGELOG.md` (Unreleased: the Console app)
- Create: `docs/assets/app/*.png` (one per page)

- [ ] **Step 1:** Rebuild the demo data (`scripts/demo_data.sh`) and run the app locally. Screenshot all ten pages at 1440×900 and compare each with the prototype's anatomy. Fix any page that misses a tile, the chart or the table.
- [ ] **Step 2:** Deploy to the sandbox as a Streamlit in Snowflake object (`streamlit/snowflake.yml`, run_id name). Open every page; the lineage is the component or the fallback, per Task 13. Record the result in `docs/app.md`.
- [ ] **Step 3:** Run the full offline suite on both venvs, all `test_app_*` tests in stvenv, and `test_app_shell.py`. Expected: all pass.
- [ ] **Step 4:** Commit `docs(app): console pages, local and Snowflake`, and drop the sandbox app object.

## Execution order

1. **Foundations:** Tasks 1 → 2 → 3, in sequence, since each defines interfaces the next uses. Task 13 runs in parallel with them.
2. **Pages:** Tasks 4 to 12 in parallel, one agent each, after Task 3.
3. **Lineage:** Task 14 after Task 13 and Task 1.
4. **Wrap-up:** Task 15 last.

A fresh reviewer checks each task before it merges; one whole-branch review comes at the end.
