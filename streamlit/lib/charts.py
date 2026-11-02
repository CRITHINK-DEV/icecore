"""Charts of the app. Each chart has a pure function over rows (unit-tested) and a thin Altair builder.

Altair is imported inside the builders only, so the pure functions run in the dbt test venvs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

Row = dict[str, Any]


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def timeline_bars(nodes: list[Row]) -> list[Row]:
    """Node timeline bars from NODE_EXECUTIONS rows: start/end as offsets in seconds from the first start.

    Uses started_at and completed_at when both exist, else started_at plus execution_time_s. Nodes without a start
    time are left out. Rows are ordered by start, then name.
    """
    timed = []
    for n in nodes:
        start = _as_dt(n.get("started_at"))
        if start is None:
            continue
        end = _as_dt(n.get("completed_at"))
        if end is None or end < start:
            end = None
        timed.append((start, end, n))
    if not timed:
        return []
    t0 = min(s for s, _, _ in timed)
    bars = []
    for start, end, n in sorted(timed, key=lambda t: (t[0], str(t[2].get("name") or t[2].get("unique_id")))):
        begin_s = (start - t0).total_seconds()
        if end is not None:
            length = (end - start).total_seconds()
        else:
            length = float(n.get("execution_time_s") or 0)
        bars.append({
            "name": n.get("name") or n.get("unique_id"),
            "unique_id": n.get("unique_id"),
            "status": n.get("status"),
            "type": n.get("type"),
            "start_s": round(begin_s, 3),
            "end_s": round(begin_s + length, 3),
            "duration_s": round(length, 3),
            "credits": n.get("credits"),
        })
    return bars


def timeline_chart(bars: list[Row]):
    """An Altair Gantt chart of timeline_bars output: one bar per node, x is seconds since the run's first node."""
    import altair as alt

    return (
        alt.Chart(alt.Data(values=bars), title="Node timeline")
        .mark_bar()
        .encode(
            x=alt.X("start_s:Q", title="Seconds since first node started"),
            x2="end_s:Q",
            y=alt.Y("name:N", sort=None, title="Node"),
            color=alt.Color("status:N", title="Status"),
            tooltip=["name:N", "type:N", "status:N", "duration_s:Q", "credits:Q"],
        )
        .properties(height=max(120, 22 * len(bars)))
    )
