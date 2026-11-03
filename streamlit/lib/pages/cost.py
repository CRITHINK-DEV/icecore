"""Pure logic of the Console Cost page: tiles, the per-model table with a trend, and the top regressions.

Rows in (INVOCATIONS, NODE_EXECUTIONS, METRICS, ALERTS_FEED as lib.queries returns them), tiles and table rows out.
The credit sums themselves come from lib/cost.py. No Streamlit and no query here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from lib import cost as costlib
from lib.console import Tile
from lib.incidents import _naive, as_of

TREND_POINTS = 15
SETTLED = "no_attribution"
SETTLED_NOTE = "settled at 0"


def _at(row: dict, key: str = "started_at") -> datetime | None:
    return _naive(row.get(key))


def _window(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [r for r in rows if (t := _at(r)) is not None and start < t <= end]


def in_window(rows: list[dict], now: datetime, days: int) -> list[dict]:
    return _window(rows, now - timedelta(days=days), now)


def _credits(rows: list[dict]) -> float:
    return sum(float(r["credits"]) for r in rows if r.get("credits") is not None)


def now_of(*row_lists: list[dict]) -> datetime:
    """The newest record's time (the dbt host's clock), not the app's clock; the app's only when there are no rows."""
    return as_of(*row_lists) or datetime.now()


def tiles(invocation_rows: list[dict], node_rows: list[dict], *, now: datetime, days: int) -> list[Tile]:
    start, prev_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    current = _credits(_window(invocation_rows, start, now))
    previous = _credits(_window(invocation_rows, prev_start, start))
    change = "—" if previous <= 0 else f"{(current - previous) / previous * 100:+.0f}%"
    window_nodes = _window(node_rows, start, now)
    models = costlib.credits_per_model(window_nodes)
    total = sum(m["credits"] for m in models)
    share = None
    share_help = "Share of the window's credits that the most expensive model used."
    if models and total > 0:
        share = f"{models[0]['name']} · {models[0]['credits'] / total * 100:.0f}%"
    unattributed = sum(1 for r in window_nodes if r.get("credits_settled") == SETTLED)
    return [
        Tile("Credits in window", f"{current:.2f}", help=f"Credits of the runs started in the last {days} days."),
        Tile("Change vs previous window", change,
             help=f"Against the {days} days before. — when there was no cost then."),
        Tile("Top model share", share, help=share_help),
        Tile("Unattributed runs", unattributed,
             help="Node runs Snowflake never attributed credits to: settled at 0, not a measured cost."),
    ]


def per_model(node_rows: list[dict], *, now: datetime, points: int = TREND_POINTS) -> list[dict]:
    """One row per costed model, biggest first: credits, runs, a `points`-day credit trend (oldest first, zero-filled)
    and `attribution`, "settled at 0" where the cost record says no_attribution (blank for a measured cost)."""
    first = (now - timedelta(days=points - 1)).date()
    days = [first + timedelta(days=i) for i in range(points)]
    trend: dict[str, dict] = {}
    settled: dict[str, list[int]] = {}
    for r in node_rows:
        if r.get("credits") is None:
            continue
        uid = r["unique_id"]
        counts = settled.setdefault(uid, [0, 0])
        counts[1] += 1
        counts[0] += r.get("credits_settled") == SETTLED
        t = _at(r)
        if t is not None and t.date() in days:
            trend.setdefault(uid, {})[t.date()] = trend.get(uid, {}).get(t.date(), 0.0) + float(r["credits"])
    out = []
    for m in costlib.credits_per_model(node_rows):
        done, total = settled[m["unique_id"]]
        note = "" if not done else SETTLED_NOTE if done == total else f"{done} of {total} runs {SETTLED_NOTE}"
        out.append({**m, "trend": [trend.get(m["unique_id"], {}).get(d, 0.0) for d in days], "attribution": note})
    return out


def regressions(metric_rows: list[dict], alert_rows: list[dict], node_rows: list[dict] | None = None,
                limit: int = costlib.TOP_N) -> list[dict]:
    """Cost regressions: the credits metric points the engines flagged above their band (worst first), then the
    ALERTS_FEED incidents of kind 'cost' for relations the metric rows do not already cover.

    An alert's node_key is `relation|credits||`, so it is deduplicated on the relation, and the node is found through
    the metric or node rows by relation (`unique_id` is None, so no row click, when it cannot be found)."""
    to_uid = {r["relation"]: r.get("node_unique_id") for r in metric_rows if r.get("relation") and r.get("node_unique_id")}
    to_uid.update({r["relation"]: r["unique_id"] for r in node_rows or [] if r.get("relation") and r.get("unique_id")})
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in costlib.top_regressions(metric_rows, limit):
        seen.add(r["relation"])
        out.append({"unique_id": to_uid.get(r["relation"]), "relation": r["relation"], "observed_at": r["observed_at"],
                    "credits": r["credits"], "expected": r["expected"], "excess": r["excess"],
                    "detail": r["engine"] or "", "source": "anomaly"})
    for a in alert_rows:
        relation = str(a.get("node_key") or "").split("|")[0]
        if a.get("kind") != "cost" or not relation or relation in seen:
            continue
        seen.add(relation)
        out.append({"unique_id": to_uid.get(relation), "relation": relation, "observed_at": a.get("last_detected_at"),
                    "credits": None, "expected": None, "excess": None, "detail": a.get("title") or "",
                    "source": "alert"})
    return out[:limit]
