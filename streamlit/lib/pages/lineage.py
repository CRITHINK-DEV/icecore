"""Lineage page logic: pure helpers over the rows and the component payload. The page is pages/lineage.py."""
from __future__ import annotations

from lib.status import label


def failed_test_options(test_results: list[dict]) -> list[dict]:
    """Latest failing (fail or error) tests, newest first: {id, name, root_cause, invocation_id, executed_at}."""
    from lib import dag

    return [{"id": t["test_unique_id"], "name": t.get("test_name") or t["test_unique_id"].split(".")[-2:][0],
             "root_cause": t.get("root_cause_unique_id"), "invocation_id": t.get("invocation_id"),
             "executed_at": t.get("executed_at")} for t in dag.failed_tests(test_results)]


def root_cause_of(test_id: str, options: list[dict], node_rows: list[dict]) -> str | None:
    """The root cause of a failed test: TEST_RESULTS.root_cause_unique_id, else the test's NODE_EXECUTIONS row."""
    from lib import dag

    opt = next((o for o in options if o["id"] == test_id), None)
    if opt and opt.get("root_cause"):
        return opt["root_cause"]
    return dag.root_cause_for(test_id, node_rows, opt.get("invocation_id") if opt else None)


def _reach(start: str, edges: list[dict], src: str, dst: str) -> int:
    nxt: dict[str, list[str]] = {}
    for e in edges:
        nxt.setdefault(e[src], []).append(e[dst])
    seen: set[str] = set()
    stack = [start]
    while stack:
        for n in nxt.get(stack.pop(), ()):
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return len(seen)


def pane_summary(node_id: str, payload: dict) -> dict | None:
    """What the right pane shows for a clicked node, from the payload alone (None for an unknown id)."""
    node = next((n for n in payload["nodes"] if n["id"] == node_id), None)
    if node is None:
        return None
    return {"id": node_id, "label": node["label"], "type": node["type"], "layer": node["layer"],
            "status": node["status"], "status_label": label(node["status"]), "tests_failing": node["tests_failing"],
            "upstream": _reach(node_id, payload["edges"], "target", "source"),
            "downstream": _reach(node_id, payload["edges"], "source", "target")}
