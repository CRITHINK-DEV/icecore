"""Settings: detected capabilities, package and dbt versions per run, engines, alert channels."""
from lib import settings
from lib.ui import empty_state, page_header

page_header("Settings & about", "Is the package configured right.", ["capabilities", "invocations"])
empty_state(
    f"Reading the icecore views from `{settings.views_schema()}` (change it in the sidebar). "
    "No capabilities recorded yet: run `dbt run-operation icecore.setup` or `icecore.doctor`."
)
