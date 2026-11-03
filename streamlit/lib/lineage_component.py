"""The lineage canvas: payload building (pure), the bundled React component, and the Graphviz fallback.

`payload` turns the lineage, node and test rows into the JSON the component draws. `lineage` shows the component (a
committed Vite build in components/lineage/build, no CDN at runtime) and returns the clicked node id. When the build
files are missing or the component fails to load, `lineage_fallback` draws the same graph with st.graphviz_chart.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from lib import dag
from lib.status import COLOURS, normalise

BUILD_DIR = Path(__file__).resolve().parent.parent / "components" / "lineage" / "build"
_FAILED = {"error", "fail", "failed"}


def _short(uid: str, name: Any) -> str:
    from lib.console import short_name  # lazy: console imports streamlit

    return str(short_name(name) or uid.split(".")[-1])


def _type(uid: str) -> str:
    return uid.split(".", 1)[0] if "." in uid else "node"


def _path(edges: list[dict], root: str, target: str) -> list[str]:
    """Shortest downstream path root -> target; the pair itself when the graph has none (a traced cause)."""
    if root == target:
        return [root]
    down: dict[str, list[str]] = {}
    for e in edges:
        down.setdefault(e["source"], []).append(e["target"])
    prev = {root: None}
    queue = deque([root])
    while queue:
        cur = queue.popleft()
        for nxt in down.get(cur, ()):
            if nxt not in prev:
                prev[nxt] = cur
                if nxt == target:
                    out = [nxt]
                    while prev[out[-1]] is not None:
                        out.append(prev[out[-1]])
                    return out[::-1]
                queue.append(nxt)
    return [root, target]


def _hood(edges: list[dict], focus: str, hops: int) -> set[str]:
    up: dict[str, list[str]] = {}
    down: dict[str, list[str]] = {}
    for e in edges:
        down.setdefault(e["source"], []).append(e["target"])
        up.setdefault(e["target"], []).append(e["source"])
    keep = {focus}
    for adj in (up, down):
        frontier = [focus]
        for _ in range(hops):
            frontier = [n for f in frontier for n in adj.get(f, ()) if n not in keep and not keep.add(n)]
    return keep


def payload(lineage_rows: list[dict], node_rows: list[dict], test_results: list[dict], *, highlight: str | None,
            root_cause: str | None, focus: str | None = None, hops: int = 2) -> dict:
    """{nodes, edges, highlight, rootCausePath, colours, hasLayers, focus, compact}.

    Each node: id, label (short name), type, layer, status (one of status.STATUSES), tests_failing. A stale source
    (inferred from freshness tests) is "late". `focus` keeps only that node and `hops` steps up and down, for the node page."""
    execs = {r["unique_id"]: r for r in dag.latest_per_node(node_rows)}
    layer_of: dict[str, str] = {}
    pairs: set[tuple[str, str]] = set()
    for e in lineage_rows:
        p, c = e.get("parent_unique_id"), e.get("child_unique_id")
        if not p or not c:
            continue
        pairs.add((p, c))
        if e.get("parent_layer"):
            layer_of.setdefault(p, e["parent_layer"])
        if e.get("child_layer"):
            layer_of.setdefault(c, e["child_layer"])
    edges = [{"source": p, "target": c} for p, c in sorted(pairs)]
    ids = {u for pc in pairs for u in pc}

    latest_tests = dag.latest_tests(test_results)
    failing: dict[str, int] = {}
    tests_of: dict[str, list[str]] = {}
    for s_, c_ in pairs:
        if c_.startswith("test."):
            tests_of.setdefault(c_, []).append(s_)
    for tid, t in latest_tests.items():
        if (t.get("status") or "").lower() in _FAILED:
            for owner in set(tests_of.get(tid, [])) | ({t["tested_unique_id"]} if t.get("tested_unique_id") else set()):
                failing[owner] = failing.get(owner, 0) + 1
    stale = dag.stale_sources([u for u in ids if u.startswith("source.")], test_results)

    nodes = []
    for uid in sorted(ids):
        ex = execs.get(uid, {})
        status = "late" if uid in stale else normalise(ex.get("status"))
        nodes.append({"id": uid, "label": _short(uid, ex.get("name")), "type": ex.get("type") or _type(uid),
                      "layer": layer_of.get(uid) or ex.get("layer") or None, "status": status,
                      "tests_failing": failing.get(uid, 0)})

    if focus and focus in ids:
        keep = _hood(edges, focus, hops)
        nodes = [n for n in nodes if n["id"] in keep]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    else:
        focus = None
    return {"nodes": nodes, "edges": edges, "highlight": highlight,
            "rootCausePath": _path(edges, root_cause, highlight) if root_cause and highlight else [],
            "colours": dict(COLOURS), "hasLayers": any(n["layer"] for n in nodes),
            "focus": focus, "compact": focus is not None}


# --- Graphviz fallback ------------------------------------------------------------------------------------------

def _esc(s: Any) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("<", "&lt;").replace(">", "&gt;")


def fallback_dot(p: dict) -> str:
    """DOT text of the payload, left to right; fills from the status colours; the root-cause path is bold and red."""
    trail = p.get("rootCausePath") or []
    on_trail = set(trail)
    hops = set(zip(trail, trail[1:]))
    red = COLOURS["failed"]
    out = ["digraph lineage {", "rankdir=LR;", 'node [shape=box, style="rounded,filled", fontcolor="white", fontsize=11];']
    for n in p["nodes"]:
        attrs = [f'label="{_esc(n["label"])}"', f'fillcolor="{COLOURS[n["status"]]}"']
        if n["id"] in on_trail:
            attrs += [f'color="{red}"', "penwidth=3"]
        if n["id"] == p.get("focus"):
            attrs += ['color="#000000"', "penwidth=4"]
        out.append(f'"{_esc(n["id"])}" [{", ".join(attrs)}];')
    seen = set()
    for e in p["edges"]:
        seen.add((e["source"], e["target"]))
        style = f' [color="{red}", penwidth=3]' if (e["source"], e["target"]) in hops else ""
        out.append(f'"{_esc(e["source"])}" -> "{_esc(e["target"])}"{style};')
    for s, t in sorted(hops - seen):  # a traced cause that is not an edge of the graph
        out.append(f'"{_esc(s)}" -> "{_esc(t)}" [color="{red}", penwidth=3, style=dashed];')
    out.append("}")
    return "\n".join(out)


def lineage_fallback(p: dict) -> None:
    import streamlit as st

    st.graphviz_chart(fallback_dot(p), width="stretch")


# --- the component ----------------------------------------------------------------------------------------------

_component = None


def use_component() -> bool:
    """True when the bundled build exists (the component renders in Streamlit in Snowflake too: Task 13, 3 Nov)."""
    return (BUILD_DIR / "index.html").is_file()


def lineage(p: dict, *, height: int = 640, key: str) -> str | None:
    """Draw the lineage; returns the id of the clicked node, or None. Falls back to Graphviz (and returns None)
    when the build files are missing or the component raises."""
    global _component
    if use_component():
        try:
            if _component is None:
                import streamlit.components.v1 as components

                _component = components.declare_component("icecore_lineage", path=str(BUILD_DIR))
            return _component(**p, height=height, key=key, default=None)
        except Exception:  # missing files at runtime, blocked iframe, protocol error: never lose the page
            pass
    lineage_fallback(p)
    return None
