"""Tests and anomalies: failures and anomalies with band charts, engine badge, shadow comparison."""
from lib.ui import empty_state, page_header

page_header("Tests & anomalies", "What to act on, which engine to trust.", ["test_results", "metrics"])
empty_state(
    "No test results yet. Add icecore tests (`icecore.volume`, `icecore.freshness`, "
    "`icecore.schema_changes`) to your models, run `dbt run-operation icecore.setup`, then `dbt test`."
)
