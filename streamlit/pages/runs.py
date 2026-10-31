"""Runs: invocation list, status, duration, credits; drill to the node timeline."""
from lib.ui import empty_state, page_header

page_header("Runs", "Is the platform healthy today, what got slower.", ["invocations", "node_executions"])
empty_state(
    "No runs yet. Add the icecore `on-run-start` and `on-run-end` hooks to your dbt project, "
    "run `dbt run-operation icecore.setup` to create the views, then run dbt."
)
