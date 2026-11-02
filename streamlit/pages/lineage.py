"""Lineage: DAG with failure / stale / schema-change overlay and root-cause highlight."""
import streamlit as st

from lib import dag, queries
from lib.ui import empty_state, page_header

page_header("Lineage", "Where the problem started.", ["lineage", "node_executions"])

edges = queries.lineage_latest()
if not edges:
    empty_state(
        "No lineage yet. Add the icecore hooks, run `dbt run-operation icecore.setup`, then run dbt. "
        "Set `root_cause: true` under `vars: icecore:` to highlight the root cause of failed tests."
    )
    st.stop()

invocation_ids = sorted({e["invocation_id"] for e in edges if e.get("invocation_id")})
node_rows = [r for i in invocation_ids for r in queries.node_executions(invocation_id=i)]
test_rows = queries.test_results()
graph = dag.build_graph(edges, node_rows, test_rows)

failing = dag.failed_tests(test_rows)
choice = st.selectbox("Failed test (highlights its root cause)", [None] + [t["test_unique_id"] for t in failing],
                      format_func=lambda v: "None" if v is None else v.split(".")[-2] if v.count(".") > 1 else v)
hl = dag.highlight(graph, choice, node_rows)
if choice and hl["root_cause"] is None:
    st.caption("No root cause recorded for this test (set `root_cause: true` and rerun).")
elif choice:
    st.caption(f"Root cause: `{hl['root_cause']}`")

stale = [n["id"] for n in graph["nodes"].values() if n["stale"]]
if stale:
    st.warning("Stale sources (from freshness tests): " + ", ".join(f"`{s}`" for s in stale))
st.graphviz_chart(dag.to_dot(graph, hl), use_container_width=True)
st.caption("Fill: red failed, amber warn, green success, grey skipped or not run. Dashed orange border: stale source.")
