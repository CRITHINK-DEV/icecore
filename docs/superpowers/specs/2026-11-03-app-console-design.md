# icecore app: the Console design

Status: chosen by the owner on 3 Nov 2026. Prototype: [docs/design/app-prototype/console.html](../../design/app-prototype/console.html) (open it in a browser next to `icecore-mock-data.js`).

## Why

The owner judged the current Streamlit pages (Runs, Lineage, Cost, SLAs) "very simple, ugly, not user friendly, nothing like Elementary", and said "we will lose badly to them". Four design directions were built on the same data from the owner's pilot project. The owner chose **D, the Console**: "the best of both worlds of A and B", a Streamlit app that runs locally first and is deployed in Snowflake later.

The other directions:
- **B, the lineage canvas**, is parked as the vision for a future Snowflake Native App ([prototype](../../design/app-prototype/native-app-vision-lineage-canvas.html)).
- **A, the triage desk**, and **C, the generated report**, are not built now. C stays the reference for a later `edr report`-style HTML report.

## Goals

- One Streamlit app with the Console's anatomy on every page: a title, 3 to 5 KPI tiles with sparklines, a filter bar, one primary chart, and one primary table, failures first. Clicking a row opens the node page.
- It runs **locally** against the account (the recommended way, PR #99: a local session from a connection name or key pair) and **in Snowflake** as a Streamlit in Snowflake object, from the same code.
- It is simple, direct, easy to navigate and interactive. A user reaches any section in one click and any node in two.
- The lineage is interactive: pan, zoom, status overlay, upstream and downstream highlight, root-cause path, layer filter, search, and a click that opens the node.

## Non-goals

- The lineage canvas as the home screen (B), and a Native App.
- A generated static HTML report.
- Any write except an acknowledgement through `ICECORE_ACK`.
- Column-level lineage, a catalog, owners, alert rules edited in the app, and ticketing.

## Anti-goals (a failure even if it works)

- Pages that look like a default Streamlit admin template: unstyled dataframes, no hierarchy, every chart the same grey.
- A page that hides a read error behind an empty state with no message.
- Anything that reads the raw event table instead of the views.
- Numbers or columns the views do not have, invented to fill a tile.

## Constraints

- **Streamlit 1.50.0**, the version pinned in `streamlit/environment.yml` and offered by the Snowflake Anaconda channel. Every widget used must exist in 1.50. The prototype's element map assumes `st.metric(chart_data=…, border=True)` and dataframe selection; check each against 1.50 and fall back where missing.
- **Packages**: only what the Snowflake Anaconda channel offers (streamlit, snowflake-snowpark-python, altair, pandas). Charts are **Altair**, which is native on both targets. No streamlit-echarts.
- **Reads** go through `streamlit/lib/queries.py` only (cached, bound parameters, `since` windows, `MAX_LIMIT`). Every read is guarded: on an exception the page shows its empty state with the error text in a caption (the rule in the agent brief; `test_app_shell.py` enforces it).
- **Pure page logic** lives in `streamlit/lib/*.py` with offline tests. Pages are thin.
- **SQL safety**: no value is ever built into SQL text. The acknowledgement is a bound `CALL ICECORE_ACK(?)` through `lib/ack.py` (story 23601af8), never an `UPDATE`.
- **Lineage component**: the only custom component. React with reactflow 11 and a dagre layout, built once into static files shipped inside the package (no CDN at runtime). It returns the clicked node id to Python.
  - **Risk to settle first**: whether Streamlit in Snowflake (warehouse runtime) can load a bundled custom component.
  - If it cannot, the Snowflake build falls back to `st.graphviz_chart` with the same status colours and root-cause path, and the local build keeps React Flow.

## The decided how

### Navigation

`st.navigation` in the sidebar, with these pages in this order:

| Group | Pages |
|---|---|
| Main | Overview, Incidents, Tests & anomalies, Runs, Schedules & freshness, Schema changes, Cost |
| Explore | Lineage, Models & sources (the node page) |
| Bottom | Settings & about |

- Counts of open problems go in page titles ("Incidents (4)").
- Page state (filters, the selected node) lives in `st.query_params`, so every view is a shareable link.
- A global node search (a `st.selectbox` over node names) opens the node page.

### Page anatomy

1. **Header**: `st.title` and a one-line `st.caption` saying what the page answers.
2. **KPI tiles**: 3 to 5 `st.metric` in `st.columns`, with a sparkline (`chart_data`), `border=True`, and a `help` text that says how the number is computed.
3. **Filter bar**: `st.segmented_control` / `st.pills` for window (7 or 15 days), layer (All / bronze / silver / gold) and status.
4. **One primary chart**: Altair, titled, axes labelled with units.
5. **One primary table**: `st.dataframe` with `column_config`:
   - status as an emoji-prefixed text column ("🔴 Failed", "🟠 Warning", "🟢 OK", "🟣 Late", "⚪ Not run");
   - sparklines as `LineChartColumn` / `BarChartColumn`;
   - progress as `ProgressColumn`;
   - failures sorted first.

   Selecting a row (`on_select="rerun"`, single row) opens the node page.

### Sections

| Page | KPI tiles | Primary chart | Primary table |
|---|---|---|---|
| Overview | open incidents, failing tests, successful runs, missed schedules, credits | runs: duration bars coloured by outcome, with failing tests on a second axis | needs attention (open incidents, errors first); then nodes that are not OK |
| Incidents | open, acknowledged, errors, oldest open | incidents over time by kind | `ALERTS_FEED` in tabs (`st.segmented_control`): Open, Acknowledged, Resolved. Acknowledge button on the selected row |
| Tests & anomalies | failing, warning, anomalies, monitors scored by Cortex | band chart of the selected metric | test and metric results, failures first, with an engine column (native / Cortex) |
| Runs | runs, failed, median duration, credits | duration per run; Gantt of the selected run (Altair `mark_bar` with x and x2) | runs; slowest nodes of the selected run |
| Schedules & freshness | declared, on time, missed, sources over limit | freshness lag per source against its limit | declared schedules (`SLA_DECLARED`) with actual cadence, last success and silent-now; missed runs (`SILENCE_EVENTS`) |
| Schema changes | changes in window, tables affected, columns added, removed or type-changed | changes per day by kind | changes (`SCHEMA_CHANGES`); the column snapshot from `SCHEMA_COLUMNS` on the node page |
| Cost | credits in window, change against the previous window, top model share, unattributed runs | daily credits (30 days) | credits per model with a trend sparkline; top regressions |
| Lineage | none (full width) | the lineage component | none. The clicked node opens a right-hand pane (`st.columns`) with a link to the node page |
| Models & sources | status, tests passing, last run, credits, freshness | the metric band charts of the node | status history, tests, schema history, cost; a small lineage neighbourhood (the component, focused) |
| Settings & about | none | none | edition, event table, Cortex availability, versions, alert channels and their last notification |

**Band chart.** An Altair area from `lower` to `upper`, a dashed line for `expected`, a line for the observed `value`, and anomalous points marked. When both engines scored a metric (shadow scoring), the native and Cortex bands are shown side by side, with their scores.

**Health score per layer** (Overview, under the tiles):
- **Components**, each from 0 to 100, from the latest run in the window:
  - tests: the mean of 100 for pass, 50 for warn and 0 for fail or error;
  - freshness: the % of sources within their limit;
  - schedules: the % of declared schedules on time;
  - anomalies: 100 minus the % of monitored metrics that are anomalous.
- **Score**: weighted, with the weights in ruling 5 below, renormalised over the components that apply (n/a ones are left out).
- **Formula** shown in the tile's `help`.

### Data the app needs that the views do not have yet

Each is a story already filed; the page shows "not recorded yet" until it lands:
- **ee1ee2c5**: a `NOTIFICATIONS` view and `INVOCATIONS.icecore_version` (Settings, Incidents).
- **94f5b653**: the tested node and `root_cause_kind` (Lineage, Tests).
- **New, filed with this design**: node layer (`dbt.node.fqn` → `LINEAGE.layer`, `NODE_EXECUTIONS.layer`) and the freshness limit (`METRICS.threshold`). See rulings 1 and 2.
- **Failing-row samples and the test SQL**: not in the views. They are not shown.

"Resolved" is derived, not stored. The rules per incident kind are in the rulings below.

### Look

- Streamlit theme in `.streamlit/config.toml`, light and dark: the prototype's neutral greys and one accent.
- Status colours used consistently: red for failed, amber for warning, green for OK, violet for late or stale, grey for not run.
- `st.html` only for the static layer strip on Overview.
- No custom CSS beyond the theme, so both targets render alike.

## Rulings from the builder check (the lead's calls, 3 Nov 2026)

1. **Layer.** A node's layer is the first folder of its dbt `fqn` under `models/`, for example `bronze`, `silver` or `gold`. Seeds, snapshots and sources keep their resource type as their layer.
   - The project may map folders to layer names with a var `icecore.layers` (`{folder: layer}`); unmapped folders show as they are.
   - No view has this yet. A new attribute `dbt.node.fqn` on node spans and lineage records, exposed as `LINEAGE.layer` and `NODE_EXECUTIONS.layer`, is a data story that comes before the pages.
   - Until it lands, the layer filter and the per-layer health score are hidden, not guessed.
2. **Freshness limit.** A source's limit is the `max_lag` of its `icecore.freshness` test. The test checks it but does not record it.
   - The same data story records it on the metric event (`icecore.metric.threshold`, in seconds) and exposes it as `METRICS.threshold`.
   - A source with no `icecore.freshness` test has no limit and counts as n/a in the health score.
3. **Base branch.** PR #99 (local mode) merges first: its local session, run script and demo data. The app work then starts from develop.
4. **Open and resolved, by kind.** An incident is open while it is not acknowledged and not resolved. Only incidents first seen in the last 7 days count in titles and tiles; older unresolved ones are listed as "stale" under Open. It is resolved:
   - **Test:** a later run of the test passes.
   - **Silence:** the node succeeds after the expected time.
   - **Anomaly and cost:** the next scored point of the same metric is inside its band.
   - **Schema change:** only when acknowledged. It is a one-off change that someone confirms.
5. **Health score.** The prototype's weights: tests 0.5, freshness 0.2, schedules 0.15, anomalies 0.15, renormalised over the components that apply. The weights are shown in the `help` text.

## Left to the builder

- Exact copy, spacing, Altair colours within the palette, which sparkline goes in each tile, the order of table columns, and how much of the prototype's detail fits each page without clutter.
- How the lineage component is packaged (Vite build committed as static files, or built in CI).

## How it is checked

- Offline tests for every `lib/*.py` function.
- `test_app_shell.py` (every page renders its empty state without a session) in the Streamlit 1.50 environment.
- A sandbox run of the app against the `ICECORE_DEMO_OBS` demo data, with a screenshot of each page compared with the prototype.
- One deploy to Snowflake in the sandbox before the work is called done.
