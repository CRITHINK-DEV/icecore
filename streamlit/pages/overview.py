"""Overview: is the platform healthy right now. Tiles, per-layer health, runs, what needs attention."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import charts, console, health, incidents, queries
from lib.pages import overview as logic

console.page("Overview", "Is the platform healthy right now.",
             ["invocations", "test_results", "alerts_feed", "node_executions"])

MAX_DAYS = max(console.WINDOWS)
LIMIT = queries.MAX_LIMIT
since = (datetime.now(timezone.utc) - timedelta(days=MAX_DAYS)).replace(tzinfo=None)

errors: list[str] = []


def read(fn, *args, **kwargs):
    rows, err = console.guarded(lambda: fn(*args, **kwargs))
    if err:
        errors.append(err)
    return rows


runs_all = read(queries.invocations, since=since, limit=queries.MAX_LIMIT)
incidents_all = read(queries.alerts_feed, since=since, limit=500)
tests_all = read(queries.test_results, since=since, limit=LIMIT)
nodes_all = read(queries.node_executions, since=since, limit=LIMIT)
metrics_all = read(queries.metrics, since=since, limit=LIMIT)
lineage_rows = read(queries.lineage_latest, limit=LIMIT)
slas = read(queries.sla_declared)
silences_all = read(queries.silence_events, since=since, limit=LIMIT)

if not (runs_all or incidents_all or tests_all or nodes_all):
    console.empty("No runs or incidents yet. Enable telemetry and run dbt to see the platform here.",
                  "; ".join(dict.fromkeys(errors)) or None)

now = incidents.as_of(runs_all, incidents_all, tests_all, nodes_all, metrics_all, silences_all) \
    or datetime.now(timezone.utc).replace(tzinfo=None)

filters = console.filter_bar("overview", layers=logic.layers_present(nodes_all, lineage_rows) or None)
runs = logic.in_window(runs_all, "started_at", now, filters.days)
tests = logic.in_window(tests_all, "executed_at", now, filters.days)
nodes = logic.in_window(nodes_all, "started_at", now, filters.days)
metrics = logic.in_window(metrics_all, "observed_at", now, filters.days)
silences = logic.in_window(silences_all, "detected_at", now, filters.days)
states = logic.with_states(incidents_all, test_results=tests_all, node_executions=nodes_all, metrics=metrics_all,
                           now=now)

st.caption(f"As of {now:%Y-%m-%d %H:%M} UTC, the newest record read.")
console.kpi_row(logic.tiles(states, runs, tests, slas, silences, runs, now=now))

strip = logic.layer_health(nodes, lineage_rows, tests, metrics, slas, silences)
if strip:
    for col, item in zip(st.columns(len(strip)), strip):
        score = item["score"]
        with col:
            st.metric(f"{item['layer']} health", "n/a" if score is None else f"{score:.0f} / 100",
                      help=health.formula_help(), border=True)

if runs:
    st.altair_chart(charts.runs_chart(logic.runs_for_chart(runs, tests)), use_container_width=True)
else:
    st.caption("No runs in this window.")

st.subheader("Needs attention")
attention = logic.needs_attention(states, now=now)
console.table(attention, {
    "status": st.column_config.TextColumn("Status"),
    "title": st.column_config.TextColumn("Incident", width="large"),
    "kind": st.column_config.TextColumn("Kind"),
    "first_detected_at": st.column_config.DatetimeColumn("First seen (UTC)", format="YYYY-MM-DD HH:mm"),
    "age": st.column_config.TextColumn("Open for"),
    "occurrences": st.column_config.NumberColumn("Times"),
    "incident_key": None,
    "incident_key": None,
    "unique_id": None,
}, key="overview_attention", limit=500)
if not attention:
    st.caption("No open incidents.")

st.subheader("Nodes that are not OK")
bad = logic.nodes_not_ok(nodes, tests)
if filters.layer:
    bad = [r for r in bad if r.get("layer") == filters.layer]
if filters.statuses:
    bad = [r for r in bad if r["status"] in filters.statuses]
console.table(bad, {
    "status": st.column_config.TextColumn("Status"),
    "name": st.column_config.TextColumn("Node", width="large"),
    "type": st.column_config.TextColumn("Type"),
    "layer": st.column_config.TextColumn("Layer"),
    "message": st.column_config.TextColumn("Message", width="large"),
    "at": st.column_config.DatetimeColumn("Last run (UTC)", format="YYYY-MM-DD HH:mm"),
    "unique_id": None,
}, key="overview_bad", limit=LIMIT)

if errors:
    st.caption("Some reads failed: " + "; ".join(dict.fromkeys(errors)))
