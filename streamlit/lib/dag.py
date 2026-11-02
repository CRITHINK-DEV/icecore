"""Lineage model for the Lineage page: pure Python, no Streamlit, no Snowflake (tested offline).

Inputs are the rows the query layer returns (LINEAGE_LATEST, NODE_EXECUTIONS, TEST_RESULTS). Outputs are a graph with a
status overlay per node, a stale flag per source, the root-cause highlight of a selected test, and a Graphviz DOT string.

Staleness: no view records "this source is stale" (the source freshness command writes no icecore record). A source is
marked stale only from freshness TESTS: a test result whose name or id mentions freshness or recency, whose latest
status is fail, error or warn, and whose id contains the source's name (the last two parts of `source.<pkg>.<src>.<tbl>`).
"""
from __future__ import annotations

from typing import Any, Iterable

FAILED = {"error", "fail", "failed"}
WARNED = {"warn", "warning"}
OK = {"success", "pass", "passed"}
SKIPPED = {"skipped", "skip", "no-op"}
FRESHNESS_WORDS = ("freshness", "recency")

COLORS = {
    "failed": "#d64545", "warn": "#e0a100", "success": "#2f9e6d", "skipped": "#8a8f98", "none": "#b8bcc4",
}


def overlay_of(status: str | None) -> str:
    """The overlay class of a dbt status: failed, warn, success, skipped or none (no execution record)."""
    s = (status or "").lower()
    if s in FAILED:
        return "failed"
    if s in WARNED:
        return "warn"
    if s in OK:
        return "success"
    if s in SKIPPED:
        return "skipped"
    return "none"


def source_key(unique_id: str) -> str:
    """`source.pkg.src.tbl` -> `src_tbl`-style match text: the last two parts joined by a dot."""
    parts = unique_id.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 3 else unique_id


def _node_type(unique_id: str) -> str:
    return unique_id.split(".", 1)[0] if "." in unique_id else "node"


def _label(unique_id: str, name: str | None) -> str:
    return name or unique_id.split(".")[-1]


def latest_tests(test_rows: Iterable[dict]) -> dict[str, dict]:
    """Latest result per test_unique_id (by executed_at)."""
    latest: dict[str, dict] = {}
    for r in test_rows:
        tid = r.get("test_unique_id")
        if not tid:
            continue
        cur = latest.get(tid)
        if cur is None or str(r.get("executed_at") or "") >= str(cur.get("executed_at") or ""):
            latest[tid] = r
    return latest


def stale_sources(source_ids: Iterable[str], test_rows: Iterable[dict]) -> dict[str, str]:
    """source unique_id -> the freshness test result status that marks it stale (only stale sources are returned)."""
    bad = [t for t in latest_tests(test_rows).values()
           if (t.get("status") or "").lower() in FAILED | WARNED
           and any(w in f"{t.get('test_name') or ''} {t.get('test_unique_id') or ''}".lower() for w in FRESHNESS_WORDS)]
    out: dict[str, str] = {}
    for sid in source_ids:
        key = source_key(sid).lower().replace(".", "_")
        for t in bad:
            hay = f"{t.get('test_unique_id') or ''} {t.get('test_name') or ''}".lower()
            if key in hay or source_key(sid).lower() in hay:
                out[sid] = (t.get("status") or "").lower()
                break
    return out


def build_graph(edges: list[dict], node_rows: list[dict], test_rows: list[dict] | None = None) -> dict[str, Any]:
    """{nodes: {id: {...}}, edges: [(parent, child)], layers: {id: int}}. Nodes: every edge end plus executed nodes."""
    execs = {r["unique_id"]: r for r in node_rows if r.get("unique_id")}
    nodes: dict[str, dict] = {}
    pairs: list[tuple[str, str]] = []
    for e in edges:
        p, c = e.get("parent_unique_id"), e.get("child_unique_id")
        if not p or not c:
            continue
        pairs.append((p, c))
        nodes.setdefault(p, {}); nodes.setdefault(c, {})
    for uid in nodes:
        ex = execs.get(uid, {})
        nodes[uid] = {
            "id": uid, "name": _label(uid, ex.get("name")), "type": ex.get("type") or _node_type(uid),
            "status": ex.get("status"), "overlay": overlay_of(ex.get("status")),
            "root_cause": ex.get("root_cause_unique_id"), "stale": False,
        }
    stale = stale_sources([u for u in nodes if u.startswith("source.")], test_rows or [])
    for uid, st_ in stale.items():
        nodes[uid]["stale"] = True
        nodes[uid]["stale_status"] = st_
    pairs = sorted(set(pairs))
    return {"nodes": nodes, "edges": pairs, "layers": layers(nodes, pairs)}


def layers(nodes: Iterable[str], pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path layer from the roots; cycle-safe (a back edge is ignored)."""
    parents: dict[str, list[str]] = {n: [] for n in nodes}
    for p, c in pairs:
        parents[c].append(p)
    memo: dict[str, int] = {}

    def depth(n: str, seen: frozenset) -> int:
        if n in memo:
            return memo[n]
        d = 0
        for p in parents[n]:
            if p not in seen:
                d = max(d, depth(p, seen | {n}) + 1)
        memo[n] = d
        return d

    return {n: depth(n, frozenset()) for n in parents}


def failed_tests(test_rows: list[dict]) -> list[dict]:
    """Latest failing (fail or error) test results, newest first."""
    bad = [t for t in latest_tests(test_rows).values() if (t.get("status") or "").lower() in FAILED]
    return sorted(bad, key=lambda t: str(t.get("executed_at") or ""), reverse=True)


def root_cause_for(test_unique_id: str, node_rows: list[dict]) -> str | None:
    """The root cause node of a test: NODE_EXECUTIONS.root_cause_unique_id of the test's row (None if not recorded)."""
    for r in node_rows:
        if r.get("unique_id") == test_unique_id and r.get("root_cause_unique_id"):
            return r["root_cause_unique_id"]
    return None


def highlight(graph: dict, test_unique_id: str | None, node_rows: list[dict]) -> dict[str, str | None]:
    """{selected, root_cause}: the node ids to emphasise when a failed test is selected."""
    if not test_unique_id:
        return {"selected": None, "root_cause": None}
    return {"selected": test_unique_id, "root_cause": root_cause_for(test_unique_id, node_rows)}


def to_dot(graph: dict, hl: dict[str, str | None] | None = None) -> str:
    """Graphviz DOT, left to right. Fill = status overlay; dashed orange border = stale; thick border = root cause."""
    hl = hl or {}
    out = ["digraph lineage {", "rankdir=LR;", 'node [shape=box, style="rounded,filled", fontcolor="white", fontsize=11];']
    for uid, n in graph["nodes"].items():
        attrs = [f'label="{_esc(n["name"])}{chr(92)}n{_esc(n["type"])}"', f'fillcolor="{COLORS[n["overlay"]]}"']
        if n["stale"]:
            attrs += ['color="#ff7a00"', "penwidth=3", 'style="rounded,filled,dashed"',
                      f'label="{_esc(n["name"])}{chr(92)}nSTALE"']
        if uid == hl.get("root_cause"):
            attrs += ['color="#7b2cbf"', "penwidth=4", f'label="ROOT CAUSE{chr(92)}n{_esc(n["name"])}"']
        if uid == hl.get("selected"):
            attrs += ['color="#000000"', "penwidth=3"]
        out.append(f'"{_esc(uid)}" [{", ".join(attrs)}];')
    for p, c in graph["edges"]:
        out.append(f'"{_esc(p)}" -> "{_esc(c)}";')
    out.append("}")
    return "\n".join(out)


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')
