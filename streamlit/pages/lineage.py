"""Lineage: DAG with failure / stale / schema-change overlay and root-cause highlight."""
import streamlit as st

from lib import dag, queries
from lib.ui import empty_state, page_header

EMPTY = ("No lineage yet. Add the icecore hooks, run `dbt run-operation icecore.setup`, then run dbt. "
         "Set `root_cause: true` under `vars: icecore:` to highlight the root cause of failed tests.")

page_header("Lineage", "Where the problem started.", ["lineage", "node_executions"])

try:
    edges = queries.lineage_latest(limit=queries.MAX_LIMIT)
    all_nodes = queries.node_executions(limit=queries.MAX_LIMIT) if edges else []
    test_rows = queries.test_results() if edges else []
except Exception as exc:  # no session, views not created yet: show the empty state, never crash
    empty_state(EMPTY)
    st.caption(f"{type(exc).__name__}: {exc}")
    st.stop()

if not edges:
    empty_state(EMPTY)
    st.stop()

# overlay: each node's most recent status, so a test-only run does not grey out the models
graph = dag.build_graph(edges, dag.latest_per_node(all_nodes), test_rows)

failing = dag.failed_tests(test_rows)
by_id = {t["test_unique_id"]: t for t in failing}
choice = st.selectbox("Failed test (highlights its root cause)", [None] + list(by_id),
                      format_func=lambda v: "None" if v is None else v.split(".")[-2] if v.count(".") > 1 else v)
hl = dag.highlight(graph, choice, all_nodes, by_id[choice].get("invocation_id") if choice else None)
if choice and hl["root_cause"] is None:
    st.caption("No root cause recorded for this test (set `root_cause: true` and rerun).")
elif choice:
    st.caption(f"Root cause: `{hl['root_cause']}`")

stale = [n["id"] for n in graph["nodes"].values() if n["stale"]]
if stale:
    st.warning("Stale sources (from freshness tests): " + ", ".join(f"`{s}`" for s in stale))
st.graphviz_chart(dag.to_dot(graph, hl), use_container_width=True)
st.caption("Fill: red failed, amber warn, green success, grey skipped or never run. Status is each node's most recent "
           "execution. Dashed orange border: stale source (inferred from freshness tests).")
