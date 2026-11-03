"""Tests & anomalies: the page's pure logic (tiles, results table, metric series per engine). No Streamlit.

Real fields, from macros/views: TEST_RESULTS has `engine` (the scoring engine of a scored test, else null), `status`
(an anomaly shows as `warn` by default), `test_unique_id`. METRICS has `anomaly_engine`, `anomaly_expected/lower/upper/
score/is_anomaly`, and `node_unique_id` (the id of the test that computed the metric). No view carries the
`icecore.shadow` event yet, so a series has two engines only when METRICS holds rows from both; a METRICS view that
later adds `shadow_engine`, `shadow_expected/lower/upper/score/is_anomaly` columns is read as the second engine.
"""
from __future__ import annotations

from typing import Any

from lib.console import Tile
from lib.status import normalise, sort_key

Row = dict[str, Any]

ENGINE_LABELS = {"native": "native", "cortex": "Cortex", "both": "both"}
NO_ENGINE = "—"
UNSCORED = "unscored"
_BAND_KEYS = ("expected", "lower", "upper", "score", "is_anomaly")


def _engine(raw: Any) -> str | None:
    text = str(raw).strip().lower() if raw else ""
    return text or None


def engine_label(engines: set[str] | list[str]) -> str:
    """native, Cortex, both, or a dash when no engine scored."""
    found = {e for e in engines if e}
    if {"native", "cortex"} <= found:
        return ENGINE_LABELS["both"]
    if len(found) == 1:
        (only,) = found
        return ENGINE_LABELS.get(only, only)
    return NO_ENGINE if not found else ENGINE_LABELS["both"]


def _split(row: Row) -> list[Row]:
    """One METRICS row as one row per scoring engine: the primary, plus the shadow when the row carries one."""
    out = [row]
    shadow = _engine(row.get("shadow_engine"))
    if shadow:
        extra = {k: v for k, v in row.items() if not k.startswith(("anomaly_", "shadow_"))}
        extra["anomaly_engine"] = shadow
        for key in _BAND_KEYS:
            extra[f"anomaly_{key}"] = row.get(f"shadow_{key}")
        out.append(extra)
    return out


def _engines_by_test(metrics: list[Row]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for m in metrics:
        for r in _split(m):
            engine, test = _engine(r.get("anomaly_engine")), r.get("node_unique_id")
            if engine and test:
                found.setdefault(str(test), set()).add(engine)
    return found


def _newer(a: Row, b: Row, key: str) -> bool:
    return b.get(key) is not None and (a.get(key) is None or b[key] > a[key])


def results_table(test_results: list[Row], metrics: list[Row]) -> list[Row]:
    """The latest result of each test, failures first, with an `engine` column (native / Cortex / both)."""
    latest: dict[str, Row] = {}
    for r in test_results or []:
        key = str(r.get("test_unique_id") or r.get("test_name") or id(r))
        if key not in latest or _newer(latest[key], r, "executed_at"):
            latest[key] = r
    engines = _engines_by_test(metrics or [])
    rows = []
    for key, r in latest.items():
        found = set(engines.get(str(r.get("test_unique_id")), set()))
        if _engine(r.get("engine")):
            found.add(_engine(r.get("engine")))
        rows.append({
            "status": normalise(r.get("status")),
            "test": r.get("test_name") or r.get("test_unique_id"),
            "type": r.get("test_type"),
            "column": r.get("column"),
            "engine": engine_label(found),
            "failures": r.get("failures"),
            "score": r.get("score"),
            "root_cause": r.get("root_cause_unique_id"),
            "executed_at": r.get("executed_at"),
            "unique_id": r.get("test_unique_id"),
        })
    return sorted(rows, key=lambda x: (sort_key(x["status"]), str(x["test"])))


def series_key(m: Row) -> tuple:
    return (m.get("relation"), m.get("metric"), m.get("column") or None, m.get("dimension") or None)


def series_label(key: tuple) -> str:
    relation, metric, column, dimension = key
    parts = [f"{metric} of {relation}"]
    if column:
        parts.append(f"column {column}")
    if dimension:
        parts.append(f"by {dimension}")
    return ", ".join(parts)


def series_for(metrics: list[Row], relation: str, metric: str, column: str | None = None,
               dimension: str | None = None) -> dict[str, list[Row]]:
    """The points of one series keyed by scoring engine, oldest first. Points no engine scored (too little history)
    join every engine's list, so the band chart still draws the value; a series nothing scored is keyed `unscored`."""
    want = (relation, metric, column or None, dimension or None)
    scored: dict[str, list[Row]] = {}
    unscored: list[Row] = []
    for m in metrics or []:
        if series_key(m) != want:
            continue
        for r in _split(m):
            engine = _engine(r.get("anomaly_engine"))
            (scored.setdefault(engine, []) if engine else unscored).append(r)
    if not scored:
        return {UNSCORED: _by_time(unscored)} if unscored else {}
    return {e: _by_time(pts + unscored) for e, pts in sorted(scored.items())}


def _by_time(points: list[Row]) -> list[Row]:
    return sorted(points, key=lambda p: (p.get("observed_at") is None, p.get("observed_at")))


def series_list(metrics: list[Row]) -> list[Row]:
    """One row per monitored series: engines, latest value, anomalous when its latest scored point is. Anomalies first."""
    groups: dict[tuple, list[Row]] = {}
    for m in metrics or []:
        groups.setdefault(series_key(m), []).append(m)
    out = []
    for key, rows in groups.items():
        points = _by_time(rows)
        engines: set[str] = set()
        for r in points:
            for s in _split(r):
                if _engine(s.get("anomaly_engine")):
                    engines.add(_engine(s["anomaly_engine"]))
        latest = points[-1]
        out.append({
            "key": key,
            "label": series_label(key),
            "relation": key[0], "metric": key[1], "column": key[2], "dimension": key[3],
            "engine": engine_label(engines),
            "engines": sorted(engines),
            "latest": latest.get("value"),
            "anomalous": bool(latest.get("anomaly_is_anomaly")),
            "anomalies": sum(1 for p in points if p.get("anomaly_is_anomaly")),
        })
    return sorted(out, key=lambda s: (not s["anomalous"], str(s["label"])))


def score_caption(points: list[Row], engine: str) -> str:
    """The score of the latest scored point of one engine's list, and its anomaly count, as text for a caption."""
    scored = [p for p in points if p.get("anomaly_score") is not None]
    name = ENGINE_LABELS.get(engine, engine)
    flagged = sum(1 for p in points if p.get("anomaly_is_anomaly"))
    if not scored:
        return f"{name}: not scored yet ({flagged} anomalies)."
    return f"{name}: latest score {float(scored[-1]['anomaly_score']):.2f}, {flagged} anomalous point(s)."


def tiles(test_results: list[Row], metrics: list[Row]) -> list[Tile]:
    """Failing, warning (latest result of each test), anomalies (series whose latest scored point is anomalous), and
    monitors scored by Cortex (series with a Cortex point)."""
    latest = results_table(test_results, metrics)
    failing = sum(1 for r in latest if r["status"] == "failed")
    warning = sum(1 for r in latest if r["status"] == "warning")
    series = series_list(metrics)
    return [
        Tile("Failing", failing, help="Tests whose latest result failed or errored."),
        Tile("Warning", warning, help="Tests whose latest result is a warning. An anomaly is a warning by default."),
        Tile("Anomalies", sum(1 for s in series if s["anomalous"]),
             help="Monitored metrics whose latest point is outside its expected band."),
        Tile("Monitors scored by Cortex", sum(1 for s in series if "cortex" in s["engines"]),
             help="Monitored metrics that the Cortex engine scored."),
    ]


def filter_status(rows: list[Row], statuses: list[str]) -> list[Row]:
    return [r for r in rows if not statuses or normalise(r.get("status")) in statuses]
