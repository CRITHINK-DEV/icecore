"""Runs: invocation list, status, duration, credits; drill to the node timeline."""
import streamlit as st

from lib import charts, queries, runs_logic as logic
from lib.ui import empty_state, page_header

page_header("Runs", "Is the platform healthy today, what got slower.", ["invocations", "node_executions"])

range_label = st.sidebar.selectbox("Date range", list(logic.RANGES), index=1, key="runs_range")
try:
    runs = queries.invocations(since=logic.since_for(range_label))
except Exception as exc:  # no session or views not created yet: show the empty state, not a crash
    empty_state(logic.ENABLE_TELEMETRY_HELP)
    st.caption(f"Could not read the runs: {exc}")
    st.stop()

if not runs:
    empty_state(logic.ENABLE_TELEMETRY_HELP)
    st.stop()

c1, c2, c3 = st.columns(3)
project = c1.selectbox("Project", [None] + logic.distinct(runs, "project"), format_func=lambda v: v or "All")
target = c2.selectbox("Target", [None] + logic.distinct(runs, "target"), format_func=lambda v: v or "All")
statuses = c3.multiselect("Status", logic.distinct(runs, "status"))
runs = logic.filter_runs(runs, project, target, statuses)

if not runs:
    empty_state("No runs match these filters. Clear a filter or widen the date range.")
    st.stop()

t = logic.totals(runs)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Runs", t["runs"])
m2.metric("Failed", t["failed"])
m3.metric("Credits", logic.credits_metric(t),
          help="Credits come from Snowflake's QUERY_ATTRIBUTION_HISTORY, which lags by hours: a run shows "
               "'pending' until its credits land. The total counts only runs that have them.")
m4.metric("Total duration (s)", f"{t['total_duration_s']:.0f}")
st.caption("Credits arrive hours after a run (Snowflake's QUERY_ATTRIBUTION_HISTORY lag); until then they show as pending.")

st.dataframe(logic.pending_credits(logic.run_table(runs)), use_container_width=True, hide_index=True)

st.subheader("Run detail")
by_label = {logic.run_label(r): r for r in runs}
chosen = by_label[st.selectbox("Run", list(by_label))]
try:
    nodes = queries.node_executions(invocation_id=chosen["invocation_id"], limit=queries.MAX_LIMIT)
except Exception as exc:
    empty_state("Could not read the node timings for this run.")
    st.caption(f"{exc}")
    st.stop()
bars = charts.timeline_bars(nodes)
if bars:
    st.altair_chart(charts.timeline_chart(bars), use_container_width=True)
    st.caption("Slowest nodes")
    st.dataframe(logic.pending_credits(logic.slowest_nodes(nodes)), use_container_width=True, hide_index=True)
else:
    empty_state("This run has no node timing records yet. Node timings arrive shortly after the run ends.")
