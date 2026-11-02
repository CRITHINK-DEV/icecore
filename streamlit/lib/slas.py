"""SLAs page logic, pure Python (no Streamlit, no Snowflake): easy to test on small lists of rows.

Inputs are the lower-case row dicts of the SILENCE_EVENTS and NODE_EXECUTIONS views. Declared schedules come
from the silence verdicts (the only view that carries meta.sla: expected_every_minutes, grace, channel,
severity); actual schedules are observed from the start times of successful node executions.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

SUCCESS = "success"

SLA_EXAMPLE = """\
models:
  - name: orders
    config:
      meta:
        sla:
          expected_every: 1h   # m, h, d or w; e.g. 15m, 1h30m, 1d
          grace: 10m           # optional, defaults to 1m (never longer than expected_every)
        alerts:
          channel: data-platform
          severity: warn       # warn or error"""

EMPTY_MESSAGE = (
    "No SLAs declared. Add `meta.sla` to the models you want watched (example below), "
    "then run `dbt run-operation icecore.setup` to schedule the silence detector."
)


def _as_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def format_minutes(minutes) -> str:
    """90 -> '1h30m', 1440 -> '1d'; None -> ''."""
    if minutes is None:
        return ""
    total = int(round(minutes))
    if total <= 0:
        return "0m"
    parts = []
    for unit, size in (("d", 1440), ("h", 60), ("m", 1)):
        n, total = divmod(total, size)
        if n:
            parts.append(f"{n}{unit}")
    return "".join(parts)


def declared_schedules(silence_rows: list[dict]) -> dict[str, dict]:
    """Latest declared schedule per model: the newest silence verdict of each unique_id."""
    latest: dict[str, dict] = {}
    for row in silence_rows:
        uid = row.get("unique_id")
        if uid is None:
            continue
        at = _as_dt(row.get("detected_at"))
        seen = latest.get(uid)
        if seen is None or (at and (seen["_at"] is None or at > seen["_at"])):
            latest[uid] = {
                "unique_id": uid,
                "expected_every_minutes": row.get("expected_every_minutes"),
                "grace_minutes": row.get("grace"),
                "channel": row.get("channel"),
                "severity": row.get("severity"),
                "_at": at,
            }
    return {uid: {k: v for k, v in d.items() if k != "_at"} for uid, d in latest.items()}


def actual_schedules(node_rows: list[dict], unique_ids=None) -> dict[str, dict]:
    """Observed schedule per node from successful executions: runs, last success, median gap in minutes."""
    starts: dict[str, list[datetime]] = {}
    for row in node_rows:
        uid = row.get("unique_id")
        if uid is None or (unique_ids is not None and uid not in unique_ids):
            continue
        if str(row.get("status") or "").lower() != SUCCESS:
            continue
        at = _as_dt(row.get("started_at")) or _as_dt(row.get("completed_at"))
        if at is not None:
            starts.setdefault(uid, []).append(at)
    out = {}
    for uid, times in starts.items():
        times.sort()
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
        out[uid] = {
            "runs": len(times),
            "last_success_at": times[-1],
            "actual_every_minutes": median(gaps) if gaps else None,
        }
    return out


def sla_schedule_table(silence_rows: list[dict], node_rows: list[dict]) -> list[dict]:
    """One row per declared model: declared schedule next to the observed one, and whether it is slower."""
    declared = declared_schedules(silence_rows)
    actual = actual_schedules(node_rows, set(declared))
    rows = []
    for uid in sorted(declared):
        d, a = declared[uid], actual.get(uid, {})
        every, observed = d["expected_every_minutes"], a.get("actual_every_minutes")
        rows.append({
            "unique_id": uid,
            "declared_every": format_minutes(every),
            "grace": format_minutes(d["grace_minutes"]),
            "actual_every": format_minutes(observed) if observed is not None else "",
            "runs_observed": a.get("runs", 0),
            "last_success_at": a.get("last_success_at"),
            "slower_than_declared": bool(every and observed and observed > every),
            "channel": d["channel"],
            "severity": d["severity"],
        })
    return rows


def silence_table(silence_rows: list[dict]) -> list[dict]:
    """Each silence event with its expected time and last success time, newest first."""
    ordered = sorted(silence_rows, key=lambda r: _as_dt(r.get("detected_at")) or datetime.min, reverse=True)
    return [{
        "unique_id": r.get("unique_id"),
        "expected_at": r.get("expected_at"),
        "last_success_at": r.get("last_success_at"),
        "detected_at": r.get("detected_at"),
        "grace": format_minutes(r.get("grace")),
        "severity": r.get("severity"),
        "channel": r.get("channel"),
    } for r in ordered]
