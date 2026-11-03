"""Incident state (derived, never stored) and "as of". Pure, no Streamlit. Rules: spec ruling 4."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lib.status import normalise

STALE_AFTER = timedelta(days=7)

_AS_OF_KEYS = ("event_at", "detected_at", "last_detected_at", "started_at", "executed_at", "observed_at", "recorded_at")


def _naive(ts):
    """Timestamps from Snowflake may be aware or naive: compare everything as naive UTC."""
    if isinstance(ts, datetime) and ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts if isinstance(ts, datetime) else None


def as_of(*row_lists: list[dict], keys=_AS_OF_KEYS) -> datetime | None:
    """The newest timestamp found in the rows (never the app's clock), or None."""
    best = None
    for rows in row_lists:
        for row in rows or []:
            for k in keys:
                ts = _naive((row or {}).get(k))
                if ts is not None and (best is None or ts > best):
                    best = ts
    return best


def age(ts: datetime, now: datetime) -> timedelta:
    """now - ts, clamped at zero (the clocks of dbt and Snowflake can disagree)."""
    a, b = _naive(now), _naive(ts)
    if a is None or b is None:
        return timedelta(0)
    return max(a - b, timedelta(0))


def _after(ts, limit) -> bool:
    ts, limit = _naive(ts), _naive(limit)
    return ts is not None and (limit is None or ts > limit)


def _series(node_key: str | None, n: int) -> list[str | None]:
    parts = (node_key or "").split("|")
    return [(parts[i] or None) if i < len(parts) else None for i in range(n)]


def _resolved(inc: dict, test_results: list[dict], node_executions: list[dict], metrics: list[dict]) -> bool:
    kind = inc.get("kind")
    key = inc.get("node_key")
    first = inc.get("first_detected_at")
    last = inc.get("last_detected_at") or first
    if kind == "test_failure":
        return any(r.get("test_unique_id") == key and normalise(r.get("status")) == "ok"
                   and _after(r.get("executed_at"), last) for r in test_results)
    if kind == "silence":
        return any(r.get("unique_id") == key and normalise(r.get("status")) == "ok"
                   and _after(r.get("started_at"), first) for r in node_executions)
    if kind in ("anomaly", "cost"):
        relation, metric, column, dimension = _series(key, 4)
        pts = [m for m in metrics
               if m.get("relation") == relation and m.get("metric") == metric
               and (m.get("column") or None) == column and (m.get("dimension") or None) == dimension
               and _after(m.get("observed_at"), last)]
        if not pts:
            return False
        nxt = min(pts, key=lambda m: _naive(m["observed_at"]))
        return not nxt.get("anomaly_is_anomaly")
    if kind == "schema_change":
        return inc.get("acknowledged_at") is not None
    return False


def state(incident: dict, *, test_results: list[dict], node_executions: list[dict], metrics: list[dict],
          now: datetime) -> str:
    """`resolved`, `acknowledged`, `open` or `stale`. Precedence: resolved > acknowledged > open (stale is an old open)."""
    if _resolved(incident, test_results or [], node_executions or [], metrics or []):
        return "resolved"
    if incident.get("acknowledged_at") is not None:
        return "acknowledged"
    first = _naive(incident.get("first_detected_at"))
    if first is not None and _naive(now) is not None and _naive(now) - first > STALE_AFTER:
        return "stale"
    return "open"


def summary(states: list[tuple[dict, str]]) -> dict:
    """Counts for titles and tiles. Stale and resolved incidents are not counted as open."""
    open_ = [i for i, s in states if s == "open"]
    firsts = [t for t in (_naive(i.get("first_detected_at")) for i in open_) if t is not None]
    return {
        "open": len(open_),
        "acknowledged": sum(1 for _, s in states if s == "acknowledged"),
        "errors_open": sum(1 for i in open_ if str(i.get("severity", "")).lower() == "error"),
        "oldest_open": min(firsts) if firsts else None,
        "stale": sum(1 for _, s in states if s == "stale"),
    }
