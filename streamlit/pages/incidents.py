"""Incidents: ALERTS_FEED grouped Open / Acknowledged / Resolved, tiles, incidents per day, and Acknowledge."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import ack, charts, console, queries
from lib.incidents import as_of
from lib.pages import incidents as logic

LIMIT = queries.MAX_LIMIT
VIEWS = ["alerts_feed"]
MESSAGE = "No incidents in this window. Incidents appear when a test fails, a metric is anomalous, a schedule is missed, a schema changes or a cost regresses."

f = console.filter_bar("incidents", layers=None, statuses=False)
since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=f.days)

feed, error = console.guarded(lambda: queries.alerts_feed(since=since, limit=LIMIT))
if error or not feed:
    console.page("Incidents", "What is open, what is acknowledged, what is resolved.", VIEWS)
    console.empty(MESSAGE, error)

# the rows that decide "resolved" (spec ruling 4); a failed read only means fewer incidents show as resolved
tests, _ = console.guarded(lambda: queries.test_results(since=since, limit=LIMIT))
nodes, _ = console.guarded(lambda: queries.node_executions(since=since, limit=LIMIT))
points, _ = console.guarded(lambda: queries.metrics(since=since, limit=LIMIT))
now = as_of(feed, tests, nodes, points) or datetime.now(timezone.utc).replace(tzinfo=None)
states = logic.classify(feed, test_results=tests, node_executions=nodes, metrics=points, now=now)
groups = logic.rows(feed, {i["incident_key"]: s for i, s in states}, now=now)

console.page(logic.title_count(states), "What is open, what is acknowledged, what is resolved.", VIEWS)
if console.truncation_note(feed, LIMIT):
    st.caption(console.truncation_note(feed, LIMIT))
console.kpi_row(logic.tiles(states, now=now))

chart_rows = logic.per_day([i for i, _ in states])
if chart_rows:
    st.altair_chart(charts.daily(chart_rows, x="day", y="incidents", title="Incidents per day by kind",
                                 y_title="Incidents first seen", color="kind"), use_container_width=True)

group = st.radio("State", list(logic.GROUPS), horizontal=True, key="incidents_group",
                 format_func=lambda g: f"{g.capitalize()} ({len(groups[g])})")
shown = groups[group]
if not shown:
    st.caption("Nothing here.")
    st.stop()

columns = ["severity", "title", "kind", "first_seen", "open_for", "times", "acknowledged_by"]
event = st.dataframe([{c: r[c] for c in columns} for r in shown], hide_index=True, width="stretch",
                     key=f"incidents_table_{group}", on_select="rerun", selection_mode="single-row",
                     column_config={"severity": "Severity", "title": "Incident", "kind": "Kind",
                                    "first_seen": st.column_config.DatetimeColumn("First seen (UTC)"),
                                    "open_for": "Open for", "times": "Times", "acknowledged_by": "Acknowledged by"})
picked = list(event.selection.rows) if event is not None else []
if picked and group == "open":
    row = shown[picked[0]]
    if st.button("Acknowledge", type="primary", key="incidents_ack"):
        text, icon, _landed = logic.acknowledge(row["incident_key"])
        st.toast(text, icon=icon)
        queries.clear_cache()
        st.rerun()
    if row["kind_key"] == "silence" and row["node_key"]:
        if st.button("Open node", key="incidents_node"):
            console.open_node(str(row["node_key"]))
elif group == "open":
    st.caption("Select a row to acknowledge it.")
