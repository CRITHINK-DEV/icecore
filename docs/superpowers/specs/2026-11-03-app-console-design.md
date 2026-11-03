# icecore app: the Console design

Status: chosen by the owner on 3 Nov 2026. Prototype: [docs/design/app-prototype/console.html](../../design/app-prototype/console.html) (open it in a browser next to `icecore-mock-data.js`).

## Why

The owner judged the current Streamlit pages (Runs, Lineage, Cost, SLAs) "very simple, ugly, not user friendly, nothing like Elementary", and said "we will lose badly to them". Four design directions were built on the same data from the owner's pilot project. The owner chose **D, the Console**: "the best of both worlds of A and B", an app that runs locally first and is deployed in Snowflake later.

The other directions:
- **B, the lineage canvas**, is parked as the vision for a future Snowflake Native App ([prototype](../../design/app-prototype/native-app-vision-lineage-canvas.html)).
- **A, the triage desk**, and **C, the generated report**, are not built now. C stays the reference for a later `edr report`-style HTML report.

## Goals

- One full-page app with the Console's anatomy on every section: a title, 3 to 5 KPI tiles with sparklines, a filter bar, one primary chart, and one primary table, failures first. Clicking a row opens the node page.
- It runs **locally** against the account (the recommended way: a local session from a connection name or key pair) and **in Snowflake** as a Streamlit in Snowflake object, from the same code.
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

## Decision of 3 Nov 2026: the app is prototype D

The first plan was native Streamlit widgets (Streamlit 1.50 with Altair, st.navigation pages, no CSS beyond the theme). The owner replaced it on 3 Nov: **the app IS prototype D, bundled as one Streamlit component.** The prototype is the product, not a model to approximate with Streamlit widgets.

- **One component.** React, ECharts and React Flow are bundled with Vite into static files in `streamlit/components/console/build/` (committed, no CDN at runtime, fonts included). The source is in `streamlit/components/console/frontend/`.
- **Data.** `streamlit/lib/console_data.py` builds the prototype's data object from the views (project, nodes, runs, node executions, test results, metrics, source freshness, schema changes, SLAs, incidents, capabilities, and a list of read errors). It is fed to the component by `streamlit/lib/console_component.py`.
- **The host page** (`streamlit/streamlit_app.py`) only fetches the data and handles the two actions the component returns: **ack** (a bound `CALL ICECORE_ACK(?)` through `lib/ack.py`) and **refresh** (clears the cache and reads again). Streamlit's own header, toolbar and sidebar are hidden, so the component fills the page.
- **In the browser.** Navigation, node pages, lineage, drawers, the window and layer filters and the light and dark theme all live in the component. The server reads 30 days once, for all layers (cached 5 minutes), and the browser filters the 7 or 15 day window and the layer.
- **Packages for Streamlit in Snowflake** stay as `streamlit/environment.yml` (Streamlit 1.50.0 and Snowpark). The component's build folder ships with the app, and the deploy steps are in [docs/app.md](../../app.md).

## Constraints

- **Reads** go through `streamlit/lib/queries.py` only (cached, bound parameters, `since` windows, `MAX_LIMIT`). Every read is guarded: a failed view is listed in the data-quality bar of the Console and the rest still renders.
- **Pure data logic** lives in `streamlit/lib/*.py` with offline tests. The host page is thin.
- **SQL safety**: no value is ever built into SQL text. The acknowledgement is a bound `CALL ICECORE_ACK(?)` through `lib/ack.py`, never an `UPDATE`.
- **No CDN**: the component loads nothing from the network, so it works in Streamlit in Snowflake.
- **The views schema** (`lib/settings.py`) is, first match wins: the `ICECORE_VIEWS_SCHEMA` env var; `views_schema` in an optional `icecore_app.toml` at the stage root; the app's own schema, if it holds `INVOCATIONS`; `<session database>.ICECORE_OBS`.

## The decided how

### Navigation

One left menu inside the component, in this order:

| Group | Sections |
|---|---|
| Main | Overview, Incidents, Tests & anomalies, Runs, Schedules & freshness, Schema changes, Cost |
| Explore | Lineage, Models & sources (the node page) |
| Bottom | Settings & about |

- Counts of open problems sit next to the section names.
- The route lives in the URL hash, so a view can be linked inside the app.
- A global search over models, sources and tests opens the node page.

### Section anatomy

1. **Header**: a title and a one-line description of what the section answers.
2. **KPI tiles**: 3 to 5, with a sparkline where the data has a series, and a help text that says how the number is computed.
3. **Filter bar**: the window (7 or 15 days) and the layer, filtered in the browser.
4. **One primary chart**, with titled, labelled axes.
5. **One primary table**, failures first. Clicking a row opens the node page.

### Sections

| Section | KPI tiles | Primary chart | Primary table |
|---|---|---|---|
| Overview | open incidents, failing tests, successful runs, missed schedules, credits | runs: duration bars coloured by outcome, with failing tests on a second axis | needs attention (open incidents, errors first); a health score per layer |
| Incidents | open, acknowledged, errors, oldest open | incidents over time by kind | `ALERTS_FEED` by state: Open, Acknowledged, Resolved. Acknowledge on a row |
| Tests & anomalies | failing, warning, anomalies, monitors scored | the selected metric with its band | test and metric results, failures first, with the engine |
| Runs | runs, failed, median duration, credits | duration per run; the timeline of the selected run | runs; slowest nodes of the selected run |
| Schedules & freshness | declared, on time, missed, sources over limit | freshness lag per source against its limit | declared schedules (`SLA_DECLARED`) and missed runs (`SILENCE_EVENTS`) |
| Schema changes | changes in window, tables affected, columns added, removed or retyped | changes per day by kind | changes (`SCHEMA_CHANGES`) |
| Cost | credits in window, change against the previous window, top model share, unattributed runs | daily credits | credits per model with a trend; top regressions |
| Lineage | node counts by status | the lineage graph (React Flow): pan, zoom, status overlay, upstream and downstream highlight, root-cause path, layer filter, search | none; a click opens the node |
| Models & sources | by node | the metric bands of the node | status history, tests, schema history, cost |
| Settings & about | none | none | edition, event table, Cortex availability, levels, versions, the data-quality list of views that could not be read |

**Health score per layer** (Overview, under the tiles):
- **Components**, each from 0 to 100, from the latest run in the window:
  - tests: the mean of 100 for pass, 50 for warn and 0 for fail or error;
  - freshness: the % of sources within their limit;
  - schedules: the % of declared schedules on time;
  - anomalies: 100 minus the % of monitored metrics that are anomalous.
- **Score**: weighted, with the weights in ruling 5 below, renormalised over the components that apply (n/a ones are left out).
- **Formula** shown in the tile's help text.

### Data the app shows

Every number comes from a view. Where a view has no data, the section says "not recorded" instead of guessing. Failing-row samples and the test SQL are not in the views, so they are not shown. "Resolved" is derived, not stored: the rules per incident kind are in ruling 4.

### Look

The prototype's look: neutral greys, one accent, a light and a dark theme, and status colours used consistently (red for failed, amber for warning, green for OK, violet for late or stale, grey for not run). The theme file `.streamlit/config.toml` only sets the accent and the status colours of the host. The component's own CSS is bundled.

## Rulings from the builder check (the lead's calls, 3 Nov 2026)

1. **Layer.** A node's layer is the first folder of its dbt `fqn` under `models/`, for example `bronze`, `silver` or `gold`. Seeds, snapshots and sources keep their resource type as their layer.
   - The project may map folders to layer names with a var `icecore.layers` (`{folder: layer}`); unmapped folders show as they are.
   - `dbt.node.fqn` and `dbt.node.layer` are recorded on node spans and lineage records and exposed as `LINEAGE.layer` and `NODE_EXECUTIONS.layer`. The layer cards, the layer filter and the per-layer health score follow the project's own layers.
2. **Freshness limit.** A source's limit is the `max_lag` of its `icecore.freshness` test. The test checks it; the metric event records it too (below).
   - It is recorded on the metric event (`icecore.metric.threshold`, in seconds) and exposed as `METRICS.threshold`.
   - A source with no `icecore.freshness` test has no limit and counts as n/a in the health score.
3. **Local mode.** The local session, run script and demo data came first, so the app could be built and checked against a real account without a Streamlit in Snowflake object.
4. **Open and resolved, by kind.** An incident is open while it is not acknowledged and not resolved. Only incidents first seen in the last 7 days count in titles and tiles; older unresolved ones are listed as "stale" under Open. It is resolved:
   - **Test:** a later run of the test passes.
   - **Silence:** the node succeeds after the expected time.
   - **Anomaly and cost:** the next scored point of the same metric is inside its band.
   - **Schema change:** only when acknowledged. It is a one-off change that someone confirms.
5. **Health score.** The prototype's weights: tests 0.5, freshness 0.2, schedules 0.15, anomalies 0.15, renormalised over the components that apply. The weights are shown in the `help` text.

## Left to the builder

- Exact copy, spacing, the order of table columns, and how much of the prototype's detail fits each section without clutter.

## How it is checked

- Offline tests for every `lib/*.py` function, including `console_data.py` and the component's contract.
- A check of the layer logic in the browser code (`integration_tests/tests/js/layers_check.mjs`).
- A sandbox run of the app against the `ICECORE_DEMO_OBS` demo data, with a screenshot of each section compared with the prototype.
- One deploy to Snowflake in the sandbox before the work is called done.
