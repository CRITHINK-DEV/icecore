"""Pure logic of the Runs page: filters, list rows, run summary. No Streamlit, no pandas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

Row = dict[str, Any]

RANGES = {"Last 24 hours": 1, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}
ENABLE_TELEMETRY_HELP = (
    "No runs in this range. To enable telemetry, add the icecore `on-run-start` and `on-run-end` hooks to your dbt "
    "project, run `dbt run-operation icecore.setup` to create the telemetry procedures and views, then run dbt. "
    "Runs show up here about a minute after they end. If you already did, widen the date range or check the views "
    "schema in the sidebar."
)


def since_for(range_label: str, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc).replace(tzinfo=None)) - timedelta(days=RANGES.get(range_label, 7))


def distinct(rows: list[Row], key: str) -> list[str]:
    return sorted({str(r[key]) for r in rows if r.get(key) is not None})


def filter_runs(rows: list[Row], project: str | None = None, target: str | None = None,
                statuses: list[str] | None = None) -> list[Row]:
    out = []
    for r in rows:
        if project and r.get("project") != project:
            continue
        if target and r.get("target") != target:
            continue
        if statuses and r.get("status") not in statuses:
            continue
        out.append(r)
    return out


def run_table(rows: list[Row]) -> list[Row]:
    """Display rows of the run list: status, duration and credits first."""
    return [{
        "status": r.get("status"),
        "started_at": r.get("started_at"),
        "command": r.get("command"),
        "project": r.get("project"),
        "target": r.get("target"),
        "duration_s": r.get("duration_s"),
        "credits": r.get("credits"),
        "nodes_failed": r.get("nodes_failed"),
        "nodes_total": r.get("nodes_total"),
        "invocation_id": r.get("invocation_id"),
    } for r in rows]


def run_label(r: Row) -> str:
    return f"{r.get('started_at')}  {r.get('command') or ''}  {r.get('status') or ''}  {str(r.get('invocation_id'))[:8]}"


def totals(rows: list[Row]) -> dict[str, Any]:
    failed = sum(1 for r in rows if str(r.get("status")).lower() in ("error", "failed", "fail"))
    return {
        "runs": len(rows),
        "failed": failed,
        "credits": sum(float(r["credits"]) for r in rows if r.get("credits") is not None),
        "credits_known": sum(1 for r in rows if r.get("credits") is not None),
        "total_duration_s": sum(float(r["duration_s"]) for r in rows if r.get("duration_s") is not None),
    }


def slowest_nodes(nodes: list[Row], n: int = 10) -> list[Row]:
    timed = [r for r in nodes if r.get("execution_time_s") is not None]
    return [{"name": r.get("name"), "status": r.get("status"), "execution_time_s": r["execution_time_s"],
             "credits": r.get("credits"), "rows_affected": r.get("rows_affected")}
            for r in sorted(timed, key=lambda r: -float(r["execution_time_s"]))[:n]]


PENDING = "pending"


def pending_credits(rows: list[Row]) -> list[Row]:
    """Display copy of rows: credits as text, 'pending' when none yet (attribution lags by hours), never None or 0."""
    return [{**r, "credits": PENDING if r.get("credits") is None else f"{float(r['credits']):.4f}"} for r in rows]


def credits_metric(t: dict[str, Any]) -> str:
    """The Credits tile: 'pending' until some run has credits, else the sum over the runs that have them."""
    return f"{t['credits']:.4f}" if t.get("credits_known") else PENDING
