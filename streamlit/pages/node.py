"""Models & sources: everything known about one node. The node comes from `?node=` (or the last one opened)."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import charts, console, queries
from lib.pages import node as logic
from lib.status import label

console.page("Models & sources", "Everything known about one node.",
             ['lineage_latest', 'node_executions', 'test_results', 'metrics', 'schema_changes', 'schema_columns'])
console.sync_node_param()  # the link stays shareable even before the reads
filters = console.filter_bar("node", layers=None, statuses=False)
since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=filters.days)

edges, error = console.guarded(lambda: queries.lineage_latest(limit=queries.MAX_LIMIT))
nodes, node_error = console.guarded(lambda: queries.node_executions(limit=queries.MAX_LIMIT, since=since))
error = error or node_error
if not edges and not nodes:
    console.empty("No nodes yet. Add the icecore hooks, run `dbt run-operation icecore.setup`, then run dbt.", error)

# the picker: every node of the latest lineage, plus every executed node
known = sorted({u for e in edges for u in (e.get("parent_unique_id"), e.get("child_unique_id")) if u}
               | {r["unique_id"] for r in nodes if r.get("unique_id")})
node = console.selected_node()
options = ([node] if node and node not in known else []) + known


def _picked() -> None:
    choice = st.session_state.get("node_picker")
    if choice:
        st.session_state[console.NODE_STATE_KEY] = choice
        st.query_params["node"] = choice


if node:
    st.session_state["node_picker"] = node  # the widget's identity is its key: follow the node opened elsewhere
st.selectbox("Node", options, index=None, placeholder="Pick a model, source or test", key="node_picker",
             on_change=_picked)
node = console.sync_node_param() if node else None
if error:
    st.caption(f"Reading the views failed: {error}")
if not node:
    st.caption("Pick a node to see its history, tests, metrics, schema and neighbours.")
    st.stop()

tests, e1 = console.guarded(lambda: queries.test_results(since=since, limit=queries.MAX_LIMIT))
lineage_rows = edges
head = logic.node_summary(node, nodes, [], [], [], lineage_rows)
if not head["found"]:
    st.info("No node with this id. Pick one above.", icon=":material/info:")
    st.stop()
relation = head["relation"]
metric_rows, e2 = console.guarded(lambda: queries.metrics(relation=relation, since=since, limit=queries.MAX_LIMIT)) \
    if relation else ([], None)
change_rows, e3 = console.guarded(lambda: queries.schema_changes(relation=relation, since=since, limit=queries.MAX_LIMIT)) \
    if relation else ([], None)
column_rows, e4 = console.guarded(lambda: logic.read_schema_columns(relation)) if relation else ([], None)
for _e in (e1, e2, e3, e4):
    if _e:
        st.caption(f"Reading the views failed: {_e}")
s = logic.node_summary(node, nodes, tests, metric_rows, change_rows, lineage_rows, column_rows)

st.subheader(console.md(s["name"]))
st.caption(f"{console.md(s['unique_id'])} · {console.md(s['type'])}"
           + (f" · {console.md(s['materialization'])}" if s["materialization"] else "")
           + (f" · layer {console.md(s['layer'])}" if s["layer"] else "")
           + (f" · {console.md(relation)}" if relation else ""))

lag = s["freshness_lag_s"]
console.kpi_row([
    console.Tile("Status", label(s["status"]), help="The status of the latest run of this node."),
    console.Tile("Tests passing", f"{s['tests_passing']}/{s['tests_total']}" if s["tests_total"] else "—",
                 help="Latest result of each test on this node."),
    console.Tile("Last run", (s["last_run"] or "—")[:16], help="Start of the latest run in the window."),
    console.Tile("Credits", f"{s['credits_total']:.3f}" if s["credits_total"] is not None else "—",
                 help="Credits attributed to this node in the window; not recorded yet when empty."),
    console.Tile("Freshness lag", f"{lag / 60:.0f} min" if lag is not None else "—",
                 help="Latest freshness_lag_seconds metric; not recorded yet when empty."),
])

st.subheader("Metrics")
if s["metric_series"]:
    for (metric, column), points in list(s["metric_series"].items())[:logic.MAX_SERIES]:
        title = f"{metric}" + (f" on {column}" if column else "")
        engine = next((p.get("anomaly_engine") for p in reversed(points) if p.get("anomaly_engine")), None)
        console.chart(charts.band_chart(points, title=title, unit=logic.METRIC_UNITS.get(metric, "value"),
                                        engine=engine), points)
        if charts.band_note(points):
            st.caption(charts.band_note(points))
else:
    st.caption("No metrics recorded for this node in the window.")

st.subheader("Status history")
if s["history"]:
    st.dataframe([{**h, "status": label(h["status"])} for h in s["history"]], hide_index=True, width="stretch",
                 column_config={"invocation_id": "Run", "started_at": "Started (UTC)", "status": "Status",
                                "execution_time_s": "Seconds", "rows_affected": "Rows", "credits": "Credits",
                                "message": "Message"})
else:
    st.caption("This node has not run in the window.")

st.subheader("Tests on this node")
if s["tests"]:
    console.table([{"test_unique_id": t["test_unique_id"], "test": t.get("test_name"), "column": t.get("column"),
                    "status": t["status"], "failures": t.get("failures"), "executed_at": t.get("executed_at")}
                   for t in s["tests"]], {"test_unique_id": None}, key="node_tests", node_column="test_unique_id")
else:
    st.caption("No test results for this node in the window.")

st.subheader("Schema")
if s["schema_history"]:
    st.dataframe([{k: c.get(k) for k in ("detected_at", "change_kind", "column", "from_type", "to_type")}
                  for c in s["schema_history"]], hide_index=True, width="stretch")
else:
    st.caption("No schema changes in the window.")
if s["columns"]:
    st.caption(f"Columns at the latest snapshot ({s['columns'][0].get('snapshot_at')}).")
    st.dataframe([{"column": c.get("column"), "data_type": c.get("data_type"), "new": bool(c.get("is_new"))}
                  for c in s["columns"]], hide_index=True, width="stretch")

st.subheader("Cost")
if s["cost_by_day"]:
    console.chart(charts.daily(s["cost_by_day"], x="day", y="credits", title="Credits per day",
                               y_title="Credits"), s["cost_by_day"])
else:
    st.caption("No credits recorded for this node in the window.")

st.subheader("Neighbourhood")
if s["parents"] or s["children"]:
    st.graphviz_chart(logic.neighbourhood_dot(s))
    console.table([{**n, "direction": d} for d, group in (("upstream", s["parents"]), ("downstream", s["children"]))
                   for n in group], {"direction": "Direction", "unique_id": "Node", "name": None, "type": "Type"},
                  key="node_neighbours")
else:
    st.caption("No upstream or downstream nodes in the latest lineage.")
