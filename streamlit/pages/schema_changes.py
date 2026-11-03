"""Schema changes: tiles, changes per day by kind, and the changes table (a row opens its node)."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import charts, console, queries
from lib.pages import schema_changes as logic

console.page("Schema changes", "What changed in the tables and columns.", ['schema_changes'])

filters = console.filter_bar("schema", layers=None, statuses=False)
rows, error = console.guarded(lambda: queries.schema_changes(
    since=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=filters.days), limit=queries.MAX_LIMIT))
if not rows:
    console.empty("No schema changes in this window.", error)

console.kpi_row(logic.tiles(rows))
per_day = logic.per_day(rows)
console.chart(charts.daily(per_day, x="day", y="changes", title="Changes per day",
                           y_title="Changes (count)", color="change_kind"), per_day)
console.table(logic.changes_table(rows), {
    "detected_at": st.column_config.DatetimeColumn("Detected (UTC)"),
    "relation": "Table",
    "change_kind": "Change",
    "column": "Column",
    "from_type": "From type",
    "to_type": "To type",
    "unique_id": None,
}, "schema_changes_table", limit=queries.MAX_LIMIT)
