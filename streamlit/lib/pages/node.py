"""Models & sources (the node page): pure logic. `node_summary` gathers everything the page shows about one node from the
rows the query layer returns, so it is unit-tested on small lists of dicts. Only `read_schema_columns` touches a query layer.
"""
from __future__ import annotations

from typing import Any

from lib.status import COLOURS, normalise

FRESHNESS_METRIC = "freshness_lag_seconds"
METRIC_UNITS = {"row_count": "rows", FRESHNESS_METRIC: "seconds", "credits": "credits"}
MAX_SERIES = 6


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _at(row: dict, *keys: str) -> str:
    for k in keys:
        if row.get(k) is not None:
            return str(row[k])
    return ""


def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and str(a).lower() == str(b).lower()


def _latest_by_node(node_rows: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for r in node_rows:
        uid = r.get("unique_id")
        if uid and (uid not in best or _at(r, "started_at", "recorded_at") >= _at(best[uid], "started_at", "recorded_at")):
            best[uid] = r
    return best


def _neighbours(uids: list[str], latest: dict[str, dict]) -> list[dict]:
    out = []
    for uid in sorted(set(uids)):
        row = latest.get(uid, {})
        out.append({"unique_id": uid, "name": row.get("name") or uid.split(".")[-1],
                    "type": row.get("type") or (uid.split(".", 1)[0] if "." in uid else "node"),
                    "status": normalise(row.get("status"))})
    return out


def _tests_of(unique_id: str, test_results: list[dict], children: list[str]) -> list[dict]:
    """The tests on a node: TEST_RESULTS.tested_unique_id when the view has it, else the test ids that depend on the node in
    LINEAGE. One row per test, its latest result."""
    if any("tested_unique_id" in r for r in test_results):
        mine = [r for r in test_results if r.get("tested_unique_id") == unique_id]
    else:
        wanted = {c for c in children if c.startswith("test.")}
        mine = [r for r in test_results if r.get("test_unique_id") in wanted]
    latest: dict[str, dict] = {}
    for r in mine:
        tid = r.get("test_unique_id")
        if tid and (tid not in latest or _at(r, "executed_at") >= _at(latest[tid], "executed_at")):
            latest[tid] = r
    rows = [{**r, "status": normalise(r.get("status"))} for r in latest.values()]
    return sorted(rows, key=lambda r: (r["status"] != "failed", str(r.get("test_unique_id"))))


def node_summary(unique_id: str, node_rows: list[dict], test_results: list[dict], metrics: list[dict],
                 schema_changes: list[dict], lineage_rows: list[dict], schema_columns: list[dict] | None = None) -> dict:
    """Everything the node page shows. Missing columns (layer, credits, tested_unique_id) degrade to None or empty."""
    mine = sorted((r for r in node_rows if r.get("unique_id") == unique_id),
                  key=lambda r: _at(r, "started_at", "recorded_at"), reverse=True)
    parents = [e["parent_unique_id"] for e in lineage_rows
               if e.get("child_unique_id") == unique_id and e.get("parent_unique_id")]
    children = [e["child_unique_id"] for e in lineage_rows
                if e.get("parent_unique_id") == unique_id and e.get("child_unique_id")]
    in_lineage = bool(parents or children) or any(unique_id in (e.get("parent_unique_id"), e.get("child_unique_id"))
                                                  for e in lineage_rows)
    head = mine[0] if mine else {}
    relation = next((r.get("relation") for r in mine if r.get("relation")), None)
    layer = next((r.get("layer") for r in mine if r.get("layer")), None)
    if layer is None:
        layer = next((e.get("parent_layer") for e in lineage_rows if e.get("parent_unique_id") == unique_id
                      and e.get("parent_layer")), None) or next(
            (e.get("child_layer") for e in lineage_rows if e.get("child_unique_id") == unique_id
             and e.get("child_layer")), None)

    history = [{"invocation_id": r.get("invocation_id"), "started_at": _at(r, "started_at", "recorded_at") or None,
                "status": normalise(r.get("status")), "execution_time_s": r.get("execution_time_s"),
                "rows_affected": r.get("rows_affected"), "credits": r.get("credits"), "message": r.get("message")}
               for r in mine]

    tests = _tests_of(unique_id, test_results, children)

    def own(r: dict) -> bool:
        return (relation is not None and _same(r.get("relation"), relation)) or r.get("node_unique_id") == unique_id

    series: dict[tuple[str, str | None], list[dict]] = {}
    freshness: list[dict] = []
    for m in metrics:
        if not own(m):
            continue
        if m.get("metric") == FRESHNESS_METRIC:
            freshness.append(m)
        series.setdefault((m.get("metric"), m.get("column")), []).append(m)
    series = {k: sorted(v, key=lambda p: _at(p, "observed_at")) for k, v in sorted(series.items(), key=lambda kv: str(kv[0]))}
    freshness.sort(key=lambda p: _at(p, "observed_at"))

    changes = sorted((c for c in schema_changes if own(c)), key=lambda c: _at(c, "detected_at"), reverse=True)

    cols = [c for c in (schema_columns or []) if relation is not None and _same(c.get("relation"), relation)]
    if cols:
        newest = max(_at(c, "snapshot_at") for c in cols)
        cols = sorted((c for c in cols if _at(c, "snapshot_at") == newest), key=lambda c: str(c.get("column")))

    by_day: dict[str, float] = {}
    for r in mine:
        credits = _num(r.get("credits"))
        day = _at(r, "started_at", "recorded_at")[:10]
        if credits is not None and day:
            by_day[day] = by_day.get(day, 0.0) + credits
    latest = _latest_by_node(node_rows)

    return {
        "found": bool(mine or in_lineage),
        "unique_id": unique_id,
        "name": head.get("name") or unique_id.split(".")[-1],
        "type": head.get("type") or (unique_id.split(".", 1)[0] if "." in unique_id else "node"),
        "materialization": head.get("materialization"),
        "relation": relation,
        "layer": layer,
        "status": normalise(head.get("status")),
        "last_run": history[0]["started_at"] if history else None,
        "history": history,
        "tests": tests,
        "tests_total": len(tests),
        "tests_passing": sum(1 for t in tests if t["status"] == "ok"),
        "metric_series": series,
        "freshness_lag_s": _num(freshness[-1].get("value")) if freshness else None,
        "schema_history": changes,
        "columns": cols,
        "credits_total": sum(by_day.values()) if by_day else None,
        "cost_by_day": [{"day": d, "credits": round(c, 6)} for d, c in sorted(by_day.items())],
        "parents": _neighbours(parents, latest),
        "children": _neighbours(children, latest),
    }


def neighbourhood_dot(summary: dict) -> str:
    """Graphviz DOT of the node and one hop up and down, left to right, fills from the status colours. The node is bold."""
    def esc(s: Any) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("<", "&lt;").replace(">", "&gt;")

    def decl(uid: str, name: str, status: str, bold: bool = False) -> str:
        extra = ', penwidth=4, color="#000000"' if bold else ""
        return f'"{esc(uid)}" [label="{esc(name)}", fillcolor="{COLOURS[status]}"{extra}];'

    out = ["digraph neighbourhood {", "rankdir=LR;",
           'node [shape=box, style="rounded,filled", fontcolor="white", fontsize=11];',
           decl(summary["unique_id"], summary["name"], summary["status"], bold=True)]
    for n in summary["parents"]:
        out += [decl(n["unique_id"], n["name"], n["status"]), f'"{esc(n["unique_id"])}" -> "{esc(summary["unique_id"])}";']
    for n in summary["children"]:
        out += [decl(n["unique_id"], n["name"], n["status"]), f'"{esc(summary["unique_id"])}" -> "{esc(n["unique_id"])}";']
    out.append("}")
    return "\n".join(out)


def read_schema_columns(relation: str, layer=None, limit: int | None = None) -> list[dict]:
    """A small guarded read of SCHEMA_COLUMNS for one relation (bound parameters). Kept local until `queries.schema_columns`
    (Task 9) is on the base; the caller wraps it in `console.guarded`."""
    from lib import queries

    layer = layer or queries.default_layer()
    schema = (layer.views_schema or "").strip()
    if not queries._SCHEMA_RE.match(schema):
        raise ValueError(f"not a schema name: {layer.views_schema!r}")
    sql = ('select relation, "COLUMN" as "column", data_type, snapshot_at, is_new, invocation_id '
           f"from {schema.upper()}.SCHEMA_COLUMNS where relation = ? order by snapshot_at desc limit ?")
    return layer.run((sql, [relation, limit or 2000]))
