"""Incidents page logic: pure functions, rows in, tiles, table rows and chart rows out. The one write is `acknowledge`."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from lib import ack, incidents as state_logic
from lib.console import Tile

STALE_MARK = "🕓 "
GROUPS = ("open", "acknowledged", "resolved")
KIND_LABEL = {"test_failure": "Test failure", "anomaly": "Anomaly", "schema_change": "Schema change",
              "silence": "Missed schedule", "cost": "Cost"}
_ICONS = {ack.OK: "✅", ack.ALREADY: "ℹ️", ack.REFUSED: "⚠️", ack.NO_PRIVILEGE: "🚫"}


def classify(incidents: list[dict], *, test_results: list[dict], node_executions: list[dict], metrics: list[dict],
             now: datetime) -> list[tuple[dict, str]]:
    """(incident, state) per incident, state from lib.incidents.state (spec ruling 4)."""
    return [(i, state_logic.state(i, test_results=test_results, node_executions=node_executions,
                                  metrics=metrics, now=now)) for i in incidents or []]


def span(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _row(incident: dict, st: str, now: datetime) -> dict[str, Any]:
    first = incident.get("first_detected_at")
    stale = st == "stale"
    title = str(incident.get("title") or incident.get("incident_key") or "")
    return {
        "incident_key": incident.get("incident_key"),
        "severity": str(incident.get("severity") or "").lower() or "—",
        "title": (STALE_MARK + title) if stale else title,  # text: st.dataframe never interprets markup
        "kind": KIND_LABEL.get(incident.get("kind"), incident.get("kind")),
        "kind_key": incident.get("kind"),
        "node_key": incident.get("node_key"),
        "first_seen": first,
        "open_for": span(state_logic.age(first, now)) if first is not None else "—",
        "times": incident.get("occurrences"),
        "acknowledged_by": incident.get("acknowledged_by") or "Nobody",
        "state": st,
        "stale": stale,
    }


def rows(incidents: list[dict], states: dict[str, str], *, now: datetime) -> dict[str, list[dict]]:
    """Open (stale last, marked), acknowledged and resolved rows. `states` maps incident_key to its state.
    Errors come first, then the oldest first-seen."""
    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for incident in incidents or []:
        st = states.get(incident.get("incident_key"), "open")
        out["open" if st == "stale" else st if st in GROUPS else "open"].append(_row(incident, st, now))

    def order(r):
        first = state_logic._naive(r["first_seen"])
        return (r["stale"], r["severity"] != "error", first or datetime.max)

    for g in GROUPS:
        out[g].sort(key=order)
    return out


def tiles(states: list[tuple[dict, str]], *, now: datetime) -> list[Tile]:
    s = state_logic.summary(states)
    oldest = s["oldest_open"]
    return [
        Tile("Open", s["open"], help="Not acknowledged and not resolved, first seen in the last 7 days."),
        Tile("Acknowledged", s["acknowledged"], help="Someone confirmed it; not yet resolved."),
        Tile("Errors", s["errors_open"], help="Open incidents with severity error."),
        Tile("Oldest open", span(state_logic.age(oldest, now)) if oldest is not None else None,
             help="Age of the oldest open incident."),
    ]


def per_day(incidents: list[dict]) -> list[dict]:
    """Incidents first seen per day (UTC) and kind, for charts.daily."""
    counts: dict[tuple[str, str], int] = {}
    for i in incidents or []:
        first = state_logic._naive(i.get("first_detected_at"))
        if first is None:
            continue
        key = (first.date().isoformat(), KIND_LABEL.get(i.get("kind"), str(i.get("kind"))))
        counts[key] = counts.get(key, 0) + 1
    return [{"day": d, "kind": k, "incidents": n} for (d, k), n in sorted(counts.items())]


def title_count(states: list[tuple[dict, str]]) -> str:
    n = state_logic.summary(states)["open"]
    return f"Incidents ({n})" if n else "Incidents"


def acknowledge(incident_key: str, layer=None) -> tuple[str, str, bool]:
    """Call lib/ack.acknowledge and return (toast text, toast icon, landed) for the page."""
    result = ack.acknowledge(incident_key, layer=layer) if layer is not None else ack.acknowledge(incident_key)
    return result.message, _ICONS.get(result.kind, "⚠️"), result.ok
