"""Overview page logic: rows in, tiles, tables and layer scores out. Pure, no Streamlit (except the Tile dataclass)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from lib import health, incidents as inc
from lib.console import Tile
from lib.status import normalise

LAYER_ORDER = ("bronze", "silver", "gold")
FRESHNESS_METRIC = "freshness_lag_seconds"
SPARK_DAYS = 7


def _dt(value: Any) -> datetime | None:
    """A timestamp as naive UTC (Snowflake may hand back aware ones), or None."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return None


def in_window(rows: list[dict], key: str, now: datetime, days: int) -> list[dict]:
    """Rows whose `key` timestamp lies within `days` days before `now`. Rows without the timestamp are dropped."""
    start = now - timedelta(days=days)
    return [r for r in rows if (t := _dt(r.get(key))) is not None and t >= start]


def with_states(incidents: list[dict], *, test_results: list[dict], node_executions: list[dict], metrics: list[dict],
                now: datetime) -> list[dict]:
    """Each incident with its derived `state` (open, acknowledged, resolved, stale)."""
    return [{**i, "state": inc.state(i, test_results=test_results, node_executions=node_executions,
                                     metrics=metrics, now=now)} for i in incidents]


def _latest(rows: list[dict], key_of, time_key: str) -> list[dict]:
    best: dict[Any, dict] = {}
    for r in rows:
        k = key_of(r)
        if k is None:
            continue
        t = _dt(r.get(time_key))
        cur = best.get(k)
        if cur is None or (t is not None and (_dt(cur.get(time_key)) is None or t >= _dt(cur.get(time_key)))):
            best[k] = r
    return list(best.values())


def _latest_tests(test_results: list[dict]) -> list[dict]:
    return _latest(test_results, lambda r: r.get("test_unique_id") or r.get("invocation_id"), "executed_at")


def _latest_nodes(node_executions: list[dict]) -> list[dict]:
    return _latest(node_executions, lambda r: r.get("unique_id"), "started_at")


def _daily(rows: list[dict], key: str, now: datetime, value) -> list[float]:
    days = [(now - timedelta(days=SPARK_DAYS - 1 - i)).date() for i in range(SPARK_DAYS)]
    totals = {d: 0.0 for d in days}
    for r in rows:
        t = _dt(r.get(key))
        if t is not None and t.date() in totals:
            totals[t.date()] += value(r)
    return [totals[d] for d in days]


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def tiles(incidents: list[dict], runs: list[dict], test_results: list[dict], slas: list[dict], silences: list[dict],
          credits_rows: list[dict], *, now: datetime) -> list[Tile]:
    """Open incidents, failing tests, successful runs, missed schedules, credits. `incidents` carry a `state`."""
    open_ = sum(1 for i in incidents if i.get("state") == "open")
    stale = sum(1 for i in incidents if i.get("state") == "stale")
    failing = sum(1 for t in _latest_tests(test_results) if normalise(t.get("status")) == "failed")
    ok_runs = [r for r in runs if normalise(r.get("status")) == "ok"]
    missed = {s.get("unique_id") for s in silences if s.get("unique_id")}
    declared = len({s.get("unique_id") for s in slas if s.get("unique_id")})
    costs = [c for c in (_num(r.get("credits")) for r in credits_rows) if c is not None]
    return [
        Tile("Open incidents", open_, delta=f"{stale} stale" if stale else None,
             help="Incidents first seen in the last 7 days that are neither acknowledged nor resolved. "
                  "Older unresolved ones are stale and not counted."),
        Tile("Failing tests", failing,
             help="Tests whose latest result in the window is a failure or an error."),
        Tile("Successful runs", f"{len(ok_runs)} of {len(runs)}",
             help="Runs that ended successfully, out of the runs in the window.",
             series=_daily(ok_runs, "started_at", now, lambda r: 1.0), chart="bar"),
        Tile("Missed schedules", len(missed) if (missed or declared) else None,
             delta=f"of {declared} declared" if declared else None,
             help="Models with a declared schedule (meta.sla) that the silence detector found silent in the window."),
        Tile("Credits", f"{sum(costs):.2f}" if costs else None,
             help="Snowflake credits attributed to the runs in the window. Not recorded until costs settle.",
             series=_daily(credits_rows, "started_at", now, lambda r: _num(r.get("credits")) or 0.0)),
    ]


def _age(ts: Any, now: datetime) -> str:
    t = _dt(ts)
    if t is None:
        return ""
    secs = int(inc.age(t, now).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return "0m"


_NODE_KINDS = ("test_failure", "silence")


def needs_attention(incidents_with_state: list[dict], *, now: datetime) -> list[dict]:
    """Open incidents (not stale, acknowledged or resolved), errors first, then oldest first seen."""
    open_ = [i for i in incidents_with_state if i.get("state") == "open"]
    open_.sort(key=lambda i: (str(i.get("severity", "")).lower() != "error",
                              _dt(i.get("first_detected_at")) or datetime.max))
    return [{
        "status": "failed" if str(i.get("severity", "")).lower() == "error" else "warning",
        "incident_key": i.get("incident_key"),
        "title": i.get("title"),
        "kind": i.get("kind"),
        "first_detected_at": _dt(i.get("first_detected_at")),
        "age": _age(i.get("first_detected_at"), now),
        "occurrences": i.get("occurrences"),
        "unique_id": i.get("node_key") if i.get("kind") in _NODE_KINDS else None,
    } for i in open_]


def nodes_not_ok(node_executions: list[dict], test_results: list[dict]) -> list[dict]:
    """The latest result of each node (and test) that is not OK, failures first."""
    out: dict[str, dict] = {}
    for r in _latest_nodes(node_executions):
        out[r["unique_id"]] = {"status": normalise(r.get("status")), "name": r.get("name") or r["unique_id"],
                               "type": r.get("type"), "layer": r.get("layer"), "message": r.get("message"),
                               "at": _dt(r.get("started_at")), "unique_id": r["unique_id"]}
    for t in _latest_tests(test_results):
        uid = t.get("test_unique_id")
        if uid and uid not in out:
            out[uid] = {"status": normalise(t.get("status")), "name": t.get("test_name") or uid, "type": "test",
                        "layer": None, "message": t.get("message"), "at": _dt(t.get("executed_at")), "unique_id": uid}
    rows = [r for r in out.values() if r["status"] != "ok"]
    from lib.status import sort_key
    return sorted(rows, key=lambda r: sort_key(r["status"]))


def runs_for_chart(runs: list[dict], test_results: list[dict]) -> list[dict]:
    """Runs with `tests_failed`, the failing tests of that run, for the right axis of charts.runs_chart."""
    failed: dict[Any, int] = {}
    for t in test_results:
        if normalise(t.get("status")) == "failed":
            failed[t.get("invocation_id")] = failed.get(t.get("invocation_id"), 0) + 1
    return [{**r, "tests_failed": failed.get(r.get("invocation_id"), 0)} for r in runs]


# --- per-layer health -------------------------------------------------------------------------------------------

def _layer_map(node_executions: list[dict], lineage: list[dict]) -> dict[str, str]:
    m: dict[str, str] = {}
    for r in lineage:
        for side in ("parent", "child"):
            if r.get(f"{side}_layer") and r.get(f"{side}_unique_id"):
                m[r[f"{side}_unique_id"]] = r[f"{side}_layer"]
    for r in _latest_nodes(node_executions):
        if r.get("layer"):
            m[r["unique_id"]] = r["layer"]
    return m


def _resolver(lmap: dict[str, str], lineage: list[dict], test_results: list[dict]):
    """uid -> layer. A test or a metric written by a test has no layer of its own: it takes the layer of the node it
    tests (TEST_RESULTS.tested_unique_id when present, else the layer of its non-test parent in LINEAGE)."""
    tested = {t["test_unique_id"]: t["tested_unique_id"] for t in test_results
              if t.get("test_unique_id") and t.get("tested_unique_id")}
    parents: dict[str, list[str]] = {}
    for e in lineage:
        if e.get("child_unique_id") and e.get("parent_unique_id"):
            parents.setdefault(e["child_unique_id"], []).append(e["parent_unique_id"])

    def layer_of(uid: str | None) -> str | None:
        if not uid:
            return None
        if lmap.get(uid):
            return lmap[uid]
        if lmap.get(tested.get(uid)):
            return lmap[tested[uid]]
        for p in parents.get(uid, []):
            if not p.startswith("test.") and lmap.get(p):
                return lmap[p]
        return None

    return layer_of


def _ordered(layers: set[str]) -> list[str]:
    return [l for l in LAYER_ORDER if l in layers] + sorted(layers - set(LAYER_ORDER))


def layers_present(node_executions: list[dict], lineage: list[dict]) -> list[str]:
    """The layers found in the rows (bronze, silver, gold first); empty when the views do not record a layer."""
    return _ordered(set(_layer_map(node_executions, lineage).values()))


def layer_health(node_executions: list[dict], lineage: list[dict], test_results: list[dict], metrics: list[dict],
                 slas: list[dict], silences: list[dict]) -> list[dict] | None:
    """[{layer, score, components}] per layer, or None when no row has a layer (the strip is then hidden)."""
    lmap = _layer_map(node_executions, lineage)
    layer_of = _resolver(lmap, lineage, test_results)
    layers = _ordered(set(lmap.values()))
    if not layers:
        return None
    tests: dict[str, list[str]] = {l: [] for l in layers}
    seen = set()
    for t in _latest_tests(test_results):
        uid = t.get("test_unique_id")
        seen.add(uid)
        if layer_of(uid):
            tests[layer_of(uid)].append(t.get("status"))
    for n in _latest_nodes(node_executions):
        if n.get("type") == "test" and n["unique_id"] not in seen and layer_of(n["unique_id"]):
            tests[layer_of(n["unique_id"])].append(n.get("status"))
    fresh: dict[str, list[bool]] = {l: [] for l in layers}
    for m in _latest([m for m in metrics if m.get("metric") == FRESHNESS_METRIC and m.get("threshold") is not None
                      and _num(m.get("value")) is not None],
                     lambda m: m.get("relation"), "observed_at"):
        if layer_of(m.get("node_unique_id")):
            fresh[layer_of(m["node_unique_id"])].append(_num(m["value"]) <= _num(m["threshold"]))
    silent = {s.get("unique_id") for s in silences}
    sched: dict[str, list[bool]] = {l: [] for l in layers}
    for s in slas:
        if lmap.get(s.get("unique_id")):
            sched[lmap[s["unique_id"]]].append(s["unique_id"] not in silent)
    anom: dict[str, list[bool]] = {l: [] for l in layers}
    scored = [m for m in metrics if m.get("anomaly_engine") and m.get("metric") != FRESHNESS_METRIC]
    for m in _latest(scored, lambda m: (m.get("relation"), m.get("metric"), m.get("column"), m.get("dimension")),
                     "observed_at"):
        if layer_of(m.get("node_unique_id")):
            anom[layer_of(m["node_unique_id"])].append(bool(m.get("anomaly_is_anomaly")))
    out = []
    for l in layers:
        comps = health.components(tests[l], fresh[l], sched[l], anom[l])
        out.append({"layer": l, "score": health.score(comps), "components": comps})
    return out
