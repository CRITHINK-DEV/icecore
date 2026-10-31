"""Cost: credits per model, per run, trend, top regressions."""
from lib.ui import empty_state, page_header

page_header("Cost", "What to optimise.", ["node_executions", "invocations"])
empty_state(
    "No cost data yet. Set `cost: true` under `vars: icecore:` in dbt_project.yml, "
    "run `dbt run-operation icecore.setup`, then run dbt."
)
