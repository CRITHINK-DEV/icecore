"""Pure logic of the Cost page: credits per model, per run, a daily trend and the top cost regressions.

No Streamlit and no query here: the page passes in the rows that lib/queries.py returned (NODE_EXECUTIONS,
INVOCATIONS, METRICS with metric 'credits'), so everything is unit-tested on small lists of dicts.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

TREND_DAYS = 30
TOP_N = 10


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def credits_per_model(node_rows: list[dict]) -> list[dict]:
    """One row per node (unique_id) with a known cost: total credits, runs costed and average per run, biggest first.

    A node whose credits are null (attribution not landed) is left out; a settled 0 counts as a costed run.
    """
    acc: dict[str, dict] = {}
    for r in node_rows:
        credits = _num(r.get("credits"))
        if credits is None:
            continue
        uid = r["unique_id"]
        row = acc.setdefault(uid, {"unique_id": uid, "name": r.get("name") or uid, "type": r.get("type"),
                                   "credits": 0.0, "runs": 0})
        row["credits"] += credits
        row["runs"] += 1
    out = list(acc.values())
    for row in out:
        row["avg_credits"] = row["credits"] / row["runs"]
    return sorted(out, key=lambda x: (-x["credits"], x["unique_id"]))


def credits_per_run(invocation_rows: list[dict]) -> list[dict]:
    """Invocations with a known cost (credits not null), newest first."""
    keep = [{"invocation_id": r["invocation_id"], "command": r.get("command"), "target": r.get("target"),
             "started_at": r.get("started_at"), "status": r.get("status"), "credits": _num(r["credits"])}
            for r in invocation_rows if r.get("credits") is not None]
    return sorted(keep, key=lambda x: (x["started_at"] is None, x["started_at"] and -x["started_at"].timestamp()))


def daily_trend(invocation_rows: list[dict], now: datetime, days: int = TREND_DAYS) -> list[dict]:
    """Credits per calendar day for the last `days` days ending at `now`, zero-filled, oldest first."""
    first = (now - timedelta(days=days - 1)).date()
    totals = {first + timedelta(days=i): 0.0 for i in range(days)}
    for r in invocation_rows:
        at, credits = r.get("started_at"), _num(r.get("credits"))
        if at is None or credits is None:
            continue
        if at.date() in totals:
            totals[at.date()] += credits
    return [{"day": d, "credits": c} for d, c in sorted(totals.items())]


def top_regressions(metric_rows: list[dict], limit: int = TOP_N) -> list[dict]:
    """Credits metric points the engines flagged (anomaly_is_anomaly true) and above their band, worst first.

    `relation` is the model's relation, or the test's unique_id for a test. Excess is value minus the expected value
    (or the upper bound when there is no expected value); a drop in cost is not a regression.
    """
    out = []
    for r in metric_rows:
        if r.get("metric") != "credits" or not r.get("anomaly_is_anomaly"):
            continue
        value = _num(r.get("value"))
        ref = _num(r.get("anomaly_expected"))
        if ref is None:
            ref = _num(r.get("anomaly_upper"))
        if value is None:
            continue
        excess = value - ref if ref is not None else None
        if excess is not None and excess <= 0:
            continue
        out.append({"relation": r["relation"], "observed_at": r.get("observed_at"), "credits": value,
                    "expected": _num(r.get("anomaly_expected")), "upper": _num(r.get("anomaly_upper")),
                    "excess": excess, "score": _num(r.get("anomaly_score")), "engine": r.get("anomaly_engine"),
                    "invocation_id": r.get("invocation_id")})
    out.sort(key=lambda x: (x["excess"] is None, -(x["excess"] or 0), -(x["score"] or 0), x["relation"]))
    return out[:limit]
