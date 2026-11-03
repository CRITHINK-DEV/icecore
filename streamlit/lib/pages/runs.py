"""Logic of the Runs page: tiles, run table, selected run, slowest nodes. Pure functions (Tile needs streamlit)."""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from lib import runs_logic
from lib.console import Tile
from lib.status import normalise

Row = dict[str, Any]

CREDITS_CAPTION = ("Credits are attributed by Snowflake hours after a run ends, so recent runs show \"pending\" "
                   "until then.")
EMPTY_MESSAGE = runs_logic.ENABLE_TELEMETRY_HELP


def _fmt_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.0f} s"


def _credits_text(value: Any) -> str:
    return "pending" if value is None else f"{float(value):.3f}"


def filter_by_status(runs: list[Row], statuses: list[str]) -> list[Row]:
    """Keep runs whose normalised status is in `statuses` (all of them when none is picked)."""
    if not statuses:
        return list(runs)
    wanted = {normalise(s) for s in statuses}
    return [r for r in runs if normalise(r.get("status")) in wanted]


def tiles(runs: list[Row]) -> list[Tile]:
    """Runs, failed, median duration, credits ("pending" while no run has its credits yet)."""
    failed = [r for r in runs if normalise(r.get("status")) == "failed"]
    durations = [float(r["duration_s"]) for r in runs if r.get("duration_s") is not None]
    known = [float(r["credits"]) for r in runs if r.get("credits") is not None]
    pending = len(runs) - len(known)
    if not runs:
        credits: Any = None
    elif not known:
        credits = "pending"
    else:
        credits = f"{sum(known):.3f}"
    return [
        Tile("Runs", len(runs)),
        Tile("Failed", len(failed), series=[float(r.get("nodes_failed") or 0) for r in runs], chart="bar"),
        Tile("Median duration", _fmt_duration(median(durations)) if durations else None, series=durations or None),
        Tile("Credits", credits, delta=f"{pending} pending" if known and pending else None,
             help=CREDITS_CAPTION),
    ]


def runs_table(runs: list[Row]) -> list[Row]:
    """Rows of the run list, newest first. `credits` reads "pending" while null."""
    ordered = sorted(runs, key=lambda r: str(r.get("started_at")), reverse=True)
    return [{
        "status": r.get("status"),
        "started_at": r.get("started_at"),
        "command": r.get("command"),
        "duration_s": r.get("duration_s"),
        "credits": _credits_text(r.get("credits")),
        "nodes_failed": r.get("nodes_failed"),
        "nodes_total": r.get("nodes_total"),
        "invocation_id": r.get("invocation_id"),
    } for r in ordered]


def selected_run(runs: list[Row], invocation_id: str | None) -> Row | None:
    """The run named by ?run= when it is in `runs`, else the newest one."""
    if not runs:
        return None
    for r in runs:
        if invocation_id and r.get("invocation_id") == invocation_id:
            return r
    return max(runs, key=lambda r: str(r.get("started_at")))


def slowest_nodes(node_rows: list[Row], n: int = 10) -> list[Row]:
    """The n slowest nodes of a run (those with a timing), each with its unique_id for the row click."""
    timed = [r for r in node_rows if r.get("execution_time_s") is not None]
    return [{
        "name": r.get("name") or r.get("unique_id"),
        "status": r.get("status"),
        "execution_time_s": r["execution_time_s"],
        "credits": _credits_text(r.get("credits")),
        "rows_affected": r.get("rows_affected"),
        "unique_id": r.get("unique_id"),
    } for r in sorted(timed, key=lambda r: -float(r["execution_time_s"]))[:n]]
