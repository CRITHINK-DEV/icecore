"""Schedules & freshness page logic: rows in, tiles, tables and chart data out. No reads, no widgets.

Declared schedules and the missed-run table reuse lib/slas.py. Sources come from METRICS rows of
`freshness_lag_seconds`; `threshold` (the limit in seconds, merged with the layer/threshold story) may be missing from
older views, in which case a source has "no limit set" and nothing is guessed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from lib import slas
from lib.console import Tile

LAG_METRIC = "freshness_lag_seconds"
NO_LIMIT = "no limit set"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def schedules_table(sla_declared: list[dict], silences: list[dict], node_rows: list[dict]) -> list[dict]:
    """One row per declared model (declared next to observed cadence). status: late when silent now, else ok."""
    rows = slas.sla_schedule_table(sla_declared, silences, node_rows)
    return [{**r, "status": "late" if r["silent_now"] else "ok"} for r in rows]


def missed_runs(silences: list[dict], since: datetime | None) -> list[dict]:
    """Silence events detected in the window, newest first."""
    rows = slas.silence_table(silences)
    if since is None:
        return rows
    return [r for r in rows if (slas._as_dt(r["detected_at"]) or datetime.min) >= since]


def sources_table(metrics: list[dict]) -> list[dict]:
    """Latest `freshness_lag_seconds` per source, with its limit when the metric carries one. Over-limit sources first."""
    latest: dict[str, dict] = {}
    for m in metrics:
        if m.get("metric") != LAG_METRIC or _num(m.get("value")) is None:
            continue
        key = m.get("relation")  # not node_unique_id: that is the freshness test's id, and tests can be several
        if key is None:
            continue
        at = slas._as_dt(m.get("observed_at")) or datetime.min
        if key not in latest or at > latest[key][0]:
            latest[key] = (at, m)
    rows = []
    for key, (at, m) in latest.items():
        lag_min = _num(m["value"]) / 60
        limit_s = _num(m.get("threshold"))
        limit_min = limit_s / 60 if limit_s is not None else None
        over = limit_min is not None and lag_min > limit_min
        relation = str(m.get("relation") or key)
        rows.append({
            "unique_id": m.get("node_unique_id") if str(m.get("node_unique_id") or "").startswith("source.") else None,
            "name": relation.rsplit(".", 1)[-1],
            "relation": relation,
            "status": "late" if over else "ok",
            "lag_minutes": lag_min,
            "data_age": slas.format_minutes(lag_min) or "0m",
            "threshold_minutes": limit_min,
            "limit": slas.format_minutes(limit_min) if limit_min is not None else NO_LIMIT,
            "over_limit": over,
            "observed_at": m.get("observed_at"),
        })
    rows.sort(key=lambda r: (not r["over_limit"], -r["lag_minutes"], r["relation"]))
    return rows


def chart_rows(sources: list[dict]) -> list[dict]:
    """Input of charts.lag_vs_limit: name, lag in minutes, threshold in minutes (None without a limit)."""
    return [{"name": r["name"], "lag_minutes": r["lag_minutes"], "threshold": r["threshold_minutes"]}
            for r in sources]


def tiles(schedule_rows: list[dict], sources: list[dict]) -> list[Tile]:
    """Declared, on time, missed, sources over limit ("—" when no source has a limit)."""
    missed = sum(1 for r in schedule_rows if r["status"] == "late")
    has_limit = any(r["threshold_minutes"] is not None for r in sources)
    return [
        Tile("Declared schedules", len(schedule_rows), help="Models with `meta.sla`."),
        Tile("On time", len(schedule_rows) - missed, help="Declared schedules with no missed run pending."),
        Tile("Missed", missed, help="The newest silence verdict has no success after it."),
        Tile("Sources over limit", sum(1 for r in sources if r["over_limit"]) if has_limit else None,
             help="Sources whose latest lag is above their `max_lag`. Sources without a limit are not counted."),
    ]
