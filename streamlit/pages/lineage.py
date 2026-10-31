"""Lineage: DAG with failure / stale / schema-change overlay and root-cause highlight."""
from lib.ui import empty_state, page_header

page_header("Lineage", "Where the problem started.", ["lineage", "node_executions"])
empty_state(
    "No lineage yet. Add the icecore hooks, run `dbt run-operation icecore.setup`, then run dbt. "
    "Set `root_cause: true` under `vars: icecore:` to highlight the root cause of failed tests."
)
