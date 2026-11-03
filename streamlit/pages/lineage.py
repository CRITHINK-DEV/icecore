"""Lineage: pan, zoom, search and layer filter on one canvas; a failed test highlights its root-cause path in red."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import console, lineage_component as lc, queries
from lib.pages import lineage as logic

EMPTY = ("No lineage yet. Add the icecore hooks, run `dbt run-operation icecore.setup`, then run dbt. "
         "Set `root_cause: true` under `vars: icecore:` to trace failed tests to their root cause.")
CANVAS_KEY = "lineage_canvas"

console.page("Lineage", "Where the problem started.", ["lineage_latest", "node_executions", "test_results"])
since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=15)

edges, error = console.guarded(lambda: queries.lineage_latest(limit=queries.MAX_LIMIT))
if not edges:
    console.empty(EMPTY, error)
nodes, node_error = console.guarded(lambda: queries.node_executions(limit=queries.MAX_LIMIT, since=since))
tests, test_error = console.guarded(lambda: queries.test_results(since=since, limit=queries.MAX_LIMIT))
for _e in (error, node_error, test_error):
    if _e:
        st.caption(f"Reading the views failed: {_e}")

options = logic.failed_test_options(tests)
names = {o["id"]: o["id"].split(".")[-2] if o["id"].count(".") > 2 else o["name"] for o in options}
choice = st.selectbox("Failed test (highlights its root-cause path)", [None] + list(names), key="lineage_test",
                      format_func=lambda v: "None" if v is None else names.get(v, v))
root = logic.root_cause_of(choice, options, nodes) if choice else None
if choice and root is None:
    st.caption("No root cause recorded for this test (set `root_cause: true` and rerun).")
elif choice:
    st.caption(f"Root cause: `{root}`")

data = lc.payload(edges, nodes, tests, highlight=choice, root_cause=root)
clicked = st.session_state.get(CANVAS_KEY)  # the id from the previous click (the component's value)
summary = logic.pane_summary(clicked, data) if clicked else None
data["selected"] = clicked if summary else None
canvas, pane = st.columns([3, 1] if summary else [1000, 1])  # same tree both ways: the iframe keeps its zoom and state
with canvas:
    lc.lineage(data, height=640, key=CANVAS_KEY)
if summary and pane is not None:
    with pane, st.container(border=True):
        st.subheader(console.md(summary["label"]))
        st.caption(console.md(summary["id"]))
        st.write(f"{summary['status_label']} · {summary['type']}" + (f" · layer {summary['layer']}" if summary["layer"] else ""))
        st.write(f"Upstream {summary['upstream']} · downstream {summary['downstream']}"
                 + (f" · {summary['tests_failing']} failing test(s)" if summary["tests_failing"] else ""))
        if st.button("Open node page", key="lineage_open", type="primary"):
            console.open_node(summary["id"])
st.caption("Click a node to highlight what feeds it and what it feeds. Colours: status of each node's latest run; "
           "purple is a stale source (from freshness tests). A red path runs from the root cause to the failed test.")
