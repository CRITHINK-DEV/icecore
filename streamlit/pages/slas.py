"""SLAs: declared schedules against actual runs, silence events."""
from lib.ui import empty_state, page_header

page_header("SLAs", "Which contracts are breached.", ["silence_events"])
empty_state(
    "No SLAs declared. Add `meta: {sla: ...}` to the models you want watched, "
    "then run `dbt run-operation icecore.setup` to schedule the silence detector."
)
