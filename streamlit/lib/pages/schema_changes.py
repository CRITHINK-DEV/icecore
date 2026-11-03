"""Schema changes: rows in, tiles, table, per-day chart data and the column snapshot out (no Streamlit widgets)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from lib import queries
from lib.console import Tile

Row = dict[str, Any]

SNAPSHOT_DAYS = 90


def _time(row: Row) -> datetime:
    value = row.get("detected_at")
    return value if isinstance(value, datetime) else datetime.min


def tiles(changes: list[Row]) -> list[Tile]:
    """Changes in the window, tables affected, and the count of each kind of change."""
    def kind(k: str) -> int:
        return sum(1 for c in changes if c.get("change_kind") == k)

    return [
        Tile("Changes", len(changes), help="Column changes detected in the window."),
        Tile("Tables affected", len({c.get("relation") for c in changes if c.get("relation")}),
             help="Tables with at least one change in the window."),
        Tile("Columns added", kind("added")),
        Tile("Columns removed", kind("removed")),
        Tile("Type changes", kind("type_changed")),
    ]


def changes_table(changes: list[Row]) -> list[dict]:
    """Newest first. `unique_id` is the node a row click opens."""
    ordered = sorted(changes, key=_time, reverse=True)
    return [{
        "detected_at": c.get("detected_at"),
        "relation": c.get("relation"),
        "change_kind": c.get("change_kind"),
        "column": c.get("column"),
        "from_type": c.get("from_type"),
        "to_type": c.get("to_type"),
        "unique_id": c.get("node_unique_id"),
    } for c in ordered]


def per_day(changes: list[Row]) -> list[dict]:
    """Changes per UTC day and kind, oldest day first. Rows without a time are left out; a missing kind is "unknown"."""
    counts: dict[tuple[str, str], int] = {}
    for c in changes:
        at = c.get("detected_at")
        if not isinstance(at, datetime):
            continue
        key = (at.date().isoformat(), c.get("change_kind") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return [{"day": d, "change_kind": k, "changes": n} for (d, k), n in sorted(counts.items())]


def latest_snapshot(rows: list[Row]) -> list[dict]:
    """The columns of the newest snapshot in SCHEMA_COLUMNS rows, by column name. `new` marks a column added since the
    previous snapshot."""
    stamped = [r for r in rows if r.get("snapshot_at") is not None]
    if not stamped:
        return []
    newest = max(r["snapshot_at"] for r in stamped)
    cols = [r for r in stamped if r["snapshot_at"] == newest]
    return [{"column": r.get("column"), "data_type": r.get("data_type"), "new": bool(r.get("is_new"))}
            for r in sorted(cols, key=lambda r: str(r.get("column")))]


def column_snapshot(relation: str) -> tuple[list[dict], str | None]:
    """For the node page: the latest column snapshot of `relation` and the read error text (None when it worked)."""
    try:
        rows = queries.schema_columns(relation, limit=queries.MAX_LIMIT,
                                      since=datetime.now() - timedelta(days=SNAPSHOT_DAYS))
    except Exception as exc:  # no session (offline) or the view is not created yet
        return [], f"{type(exc).__name__}: {exc}"
    return latest_snapshot(list(rows)), None
