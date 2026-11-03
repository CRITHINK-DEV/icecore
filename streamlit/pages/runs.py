"""Runs: the runs of the window, the selected run's Gantt and its slowest nodes."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import charts, console, queries
from lib.pages import runs as logic

console.page("Runs", "Is the platform healthy today, what got slower.", ["invocations", "node_executions"])

filters = console.filter_bar("runs", layers=None)
since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=filters.days)

all_runs, error = console.guarded(lambda: queries.invocations(since=since, limit=queries.MAX_LIMIT))
if error or not all_runs:
    console.empty(logic.EMPTY_MESSAGE, error)
runs = logic.filter_by_status(all_runs, filters.statuses)
if not runs:
    console.empty("No runs match these filters. Clear a filter or widen the window.")

console.kpi_row(logic.tiles(runs))
st.caption(logic.CREDITS_CAPTION)
note = console.truncation_note(all_runs, queries.MAX_LIMIT)
if note:
    st.caption(note)

st.altair_chart(charts.runs_chart(runs), use_container_width=True)

st.subheader("Runs")
picked = console.table(
    logic.runs_table(runs),
    {"started_at": st.column_config.DatetimeColumn("Started"),
     "duration_s": st.column_config.NumberColumn("Duration (s)", format="%.1f"),
     "nodes_failed": st.column_config.NumberColumn("Failed nodes"),
     "nodes_total": st.column_config.NumberColumn("Nodes")},
    key="runs_table", node_column=None)
if picked and st.query_params.get("run") != picked["invocation_id"]:
    st.query_params["run"] = picked["invocation_id"]

run = logic.selected_run(runs, st.query_params.get("run"))
st.subheader(f"Run {run['started_at']}")
st.caption(f"`{run['invocation_id']}`")
nodes, node_error = console.guarded(
    lambda: queries.node_executions(invocation_id=run["invocation_id"], since=since, limit=queries.MAX_LIMIT))
if node_error:
    st.caption(f"Reading the node timings failed: {node_error}")
elif not any(n.get("started_at") for n in nodes):
    st.info("This run has no node timing records yet. They arrive shortly after the run ends.",
            icon=":material/info:")
else:
    st.altair_chart(charts.gantt(nodes), use_container_width=True)
    st.subheader("Slowest nodes")
    console.table(
        logic.slowest_nodes(nodes, 10),
        {"execution_time_s": st.column_config.NumberColumn("Seconds", format="%.1f"),
         "rows_affected": st.column_config.NumberColumn("Rows"),
         "unique_id": None},
        key="runs_slowest", limit=None)
