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


# --- the Console chart library ------------------------------------------------------------------------------------
# Every chart is titled, both axes carry a title with the unit, colours come from lib.status.COLOURS, and times are
# UTC as stored (naive timestamps are read as UTC). Empty rows give an empty chart, never an error.

def _utc_iso(value: Any) -> str | None:
    """An ISO 8601 string marked UTC (trailing Z) so Vega-Lite does not shift it into the browser's timezone."""
    dt = _as_dt(value)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone

        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _first(row: Row, *keys: str) -> Any:
    for k in keys:
        if row.get(k) is not None:
            return row[k]
    return None


def _status_scale():
    import altair as alt

    from lib.status import COLOURS, STATUSES

    return alt.Scale(domain=list(STATUSES), range=[COLOURS[s] for s in STATUSES])


def _time_x(field: str, title: str = "Time (UTC)"):
    import altair as alt

    return alt.X(f"{field}:T", title=title, scale=alt.Scale(type="utc"))


def band_chart(points: list[Row], *, title: str, unit: str, engine: str | None = None):
    """METRICS rows as value over time: the expected band (lower to upper), the expected line, the value line, and red
    points where the engine flagged an anomaly."""
    import altair as alt

    from lib.status import COLOURS

    data = []
    for p in points:
        at = _utc_iso(p.get("observed_at"))
        if at is None:
            continue
        data.append({
            "observed_at": at,
            "value": _num(p.get("value")),
            "expected": _num(_first(p, "anomaly_expected", "expected")),
            "lower": _num(_first(p, "anomaly_lower", "lower")),
            "upper": _num(_first(p, "anomaly_upper", "upper")),
            "anomaly_is_anomaly": bool(p.get("anomaly_is_anomaly")),
        })
    data.sort(key=lambda d: d["observed_at"])
    y_title = f"Value ({unit})"
    x = _time_x("observed_at")
    base = alt.Chart(alt.Data(values=data))
    band = base.mark_area(opacity=0.2, color=COLOURS["late"]).encode(
        x=x, y=alt.Y("lower:Q", title=y_title), y2="upper:Q")
    expected = base.mark_line(strokeDash=[4, 4], color=COLOURS["not_run"]).encode(
        x=x, y=alt.Y("expected:Q", title=y_title))
    value = base.mark_line(color=COLOURS["ok"]).encode(
        x=x, y=alt.Y("value:Q", title=y_title), tooltip=["observed_at:T", "value:Q", "expected:Q", "lower:Q", "upper:Q"])
    points_layer = (
        base.mark_point(color=COLOURS["failed"], filled=True, size=70)
        .transform_filter("datum.anomaly_is_anomaly")
        .encode(x=x, y=alt.Y("value:Q", title=y_title))
    )
    chart_title = alt.TitleParams(title, subtitle=f"Engine: {engine}" if engine else "")
    return alt.layer(band, expected, value, points_layer).properties(title=chart_title)


def runs_chart(runs: list[Row]):
    """One bar per run (duration, coloured by normalised status) and a line of failing tests on a right axis."""
    import altair as alt

    from lib.status import normalise

    data = []
    for r in runs:
        at = _utc_iso(r.get("started_at"))
        if at is None:
            continue
        data.append({
            "started_at": at,
            "duration_s": _num(r.get("duration_s")),
            "status": normalise(r.get("status")),
            "failing_tests": _num(_first(r, "tests_failed", "nodes_failed")) or 0,
            "invocation_id": r.get("invocation_id"),
        })
    data.sort(key=lambda d: d["started_at"])
    base = alt.Chart(alt.Data(values=data))
    x = _time_x("started_at", "Run start (UTC)")
    bars = base.mark_bar().encode(
        x=x,
        y=alt.Y("duration_s:Q", title="Duration (seconds)"),
        color=alt.Color("status:N", title="Status", scale=_status_scale()),
        tooltip=["invocation_id:N", "started_at:T", "duration_s:Q", "status:N", "failing_tests:Q"],
    )
    line = base.mark_line(point=True, color=_status_colour("failed")).encode(
        x=x, y=alt.Y("failing_tests:Q", title="Failing tests (count)", axis=alt.Axis(orient="right")))
    return alt.layer(bars, line).resolve_scale(y="independent").properties(title="Runs: duration and failing tests")


def _status_colour(status: str) -> str:
    from lib.status import COLOURS

    return COLOURS[status]


def gantt(nodes: list[Row]):
    """Node executions as bars: x from started_at to started_at plus execution_time_s, y the node name sorted by start."""
    import altair as alt

    from datetime import timedelta

    from lib.status import normalise

    timed = []
    for n in nodes:
        start = _as_dt(n.get("started_at"))
        if start is None:
            continue
        end = start + timedelta(seconds=_num(n.get("execution_time_s")) or 0)
        timed.append((start, {
            "name": str(n.get("name") or n.get("unique_id")),
            "started_at": _utc_iso(start),
            "ended_at": _utc_iso(end),
            "status": normalise(n.get("status")),
            "execution_time_s": _num(n.get("execution_time_s")) or 0,
        }))
    timed.sort(key=lambda t: (t[0], t[1]["name"]))
    data = [d for _, d in timed]
    return (
        alt.Chart(alt.Data(values=data), title="Node timeline")
        .mark_bar()
        .encode(
            x=alt.X("started_at:T", title="Time (UTC)", scale=alt.Scale(type="utc")),
            x2="ended_at:T",
            y=alt.Y("name:N", sort=[d["name"] for d in data], title="Node"),
            color=alt.Color("status:N", title="Status", scale=_status_scale()),
            tooltip=["name:N", "status:N", "started_at:T", "execution_time_s:Q"],
        )
        .properties(height=max(120, 22 * len(data)))
    )


def daily(rows: list[Row], *, x: str, y: str, title: str, y_title: str, color: str | None = None):
    """Bars per day: x is a date field, y a number, optionally stacked by `color`."""
    import altair as alt

    data = [{**r, x: _utc_iso(r.get(x)) or r.get(x)} for r in rows]
    enc = {
        "x": alt.X(f"{x}:T", title="Day (UTC)", scale=alt.Scale(type="utc")),
        "y": alt.Y(f"{y}:Q", title=y_title),
        "tooltip": [f"{x}:T", f"{y}:Q"] + ([f"{color}:N"] if color else []),
    }
    if color:
        enc["color"] = alt.Color(f"{color}:N", title=color.replace("_", " ").capitalize())
    return alt.Chart(alt.Data(values=data), title=title).mark_bar().encode(**enc)


def lag_vs_limit(sources: list[Row]):
    """Bars of each source's lag in minutes, with a tick at its threshold when it has one."""
    import altair as alt

    data = []
    for s in sources:
        lag = _num(_first(s, "lag_minutes"))
        if lag is None and _num(_first(s, "lag_s", "lag_seconds")) is not None:
            lag = _num(_first(s, "lag_s", "lag_seconds")) / 60
        data.append({
            "name": str(s.get("name") or s.get("unique_id")),
            "lag_minutes": lag,
            "threshold": _num(s.get("threshold")),
        })
    base = alt.Chart(alt.Data(values=data))
    y = alt.Y("name:N", title="Source", sort=None)
    bars = base.mark_bar(color=_status_colour("late")).encode(
        x=alt.X("lag_minutes:Q", title="Lag (minutes)"), y=y, tooltip=["name:N", "lag_minutes:Q", "threshold:Q"])
    tick = base.mark_tick(color=_status_colour("failed"), thickness=3, size=18).encode(
        x=alt.X("threshold:Q", title="Lag (minutes)"), y=y)
    return alt.layer(bars, tick).properties(title="Source lag against its limit")
