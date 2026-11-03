"""Lineage component payload, fallback and page (Task 14). Needs streamlit: run from the Streamlit 1.50 venv with --noconftest."""
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
PAGE = str(APP_DIR / "pages" / "lineage.py")
SRC, STG, MART, TEST = "source.shop.raw.orders", "model.shop.stg_orders", "model.shop.orders", "test.shop.not_null_orders_id.abc"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


EDGES = [
    {"parent_unique_id": SRC, "child_unique_id": STG, "parent_layer": "raw", "child_layer": "silver"},
    {"parent_unique_id": STG, "child_unique_id": MART, "parent_layer": "silver", "child_layer": "gold"},
    {"parent_unique_id": MART, "child_unique_id": TEST, "parent_layer": "gold", "child_layer": None},
]
NODES = [
    {"unique_id": STG, "name": "stg_orders", "type": "model", "status": "error", "started_at": "2026-11-03 06:00:00",
     "invocation_id": "i1"},
    {"unique_id": MART, "name": "orders", "type": "model", "status": "success", "started_at": "2026-11-03 06:00:00",
     "invocation_id": "i1"},
    {"unique_id": TEST, "name": "not_null", "type": "test", "status": "fail", "started_at": "2026-11-03 06:00:01",
     "invocation_id": "i1", "root_cause_unique_id": STG},
]
TESTS = [{"test_unique_id": TEST, "test_name": "not_null", "status": "fail", "executed_at": "2026-11-03 06:00:01",
          "invocation_id": "i1", "root_cause_unique_id": STG}]


def test_payload_statuses_and_root_cause_path():
    from lib import lineage_component as lc

    p = lc.payload(EDGES, NODES, TESTS, highlight=TEST, root_cause=STG)
    by = {n["id"]: n for n in p["nodes"]}
    assert by[STG]["status"] == "failed" and by[MART]["status"] == "ok" and by[SRC]["status"] == "not_run"
    assert by[MART]["tests_failing"] == 1 and by[STG]["label"] == "stg_orders"
    assert p["rootCausePath"] == [STG, MART, TEST]
    assert p["highlight"] == TEST
    assert {(e["source"], e["target"]) for e in p["edges"]} == {(SRC, STG), (STG, MART), (MART, TEST)}
    assert p["colours"]["failed"].startswith("#")


def test_payload_without_highlight_has_no_path():
    from lib import lineage_component as lc

    p = lc.payload(EDGES, NODES, TESTS, highlight=None, root_cause=None)
    assert p["rootCausePath"] == [] and p["highlight"] is None


def test_payload_root_cause_not_connected_falls_back_to_the_pair():
    from lib import lineage_component as lc

    p = lc.payload(EDGES, NODES, TESTS, highlight=TEST, root_cause="source.shop.other.x")
    assert p["rootCausePath"] == ["source.shop.other.x", TEST]


def test_payload_without_layer():
    from lib import lineage_component as lc

    bare = [{"parent_unique_id": e["parent_unique_id"], "child_unique_id": e["child_unique_id"]} for e in EDGES]
    p = lc.payload(bare, NODES, TESTS, highlight=None, root_cause=None)
    assert all(n["layer"] is None for n in p["nodes"]) and not p["hasLayers"]
    assert lc.payload(EDGES, NODES, TESTS, highlight=None, root_cause=None)["hasLayers"]


def test_payload_focus_keeps_the_neighbourhood():
    from lib import lineage_component as lc

    p = lc.payload(EDGES, NODES, TESTS, highlight=None, root_cause=None, focus=STG, hops=1)
    assert {n["id"] for n in p["nodes"]} == {SRC, STG, MART} and p["focus"] == STG


def test_lineage_payload_2000_nodes():
    from lib import lineage_component as lc

    edges = [{"parent_unique_id": f"model.p.m{i}", "child_unique_id": f"model.p.m{i + 1}", "parent_layer": "silver",
              "child_layer": "gold"} for i in range(1999)]
    nodes = [{"unique_id": f"model.p.m{i}", "name": f"m{i}", "type": "model", "status": "success",
              "started_at": "2026-11-03 06:00:00"} for i in range(2000)]
    t = time.perf_counter()
    p = lc.payload(edges, nodes, [], highlight="model.p.m1999", root_cause="model.p.m0")
    took = time.perf_counter() - t
    assert len(p["nodes"]) == 2000
    assert took < 1.0, took
    assert len(json.dumps(p)) < 2_000_000


def test_fallback_dot_escapes_names():
    from lib import lineage_component as lc

    p = lc.payload([{"parent_unique_id": 'a"b<c', "child_unique_id": STG}], [], [], highlight=None, root_cause=None)
    dot = lc.fallback_dot(p)
    assert '"a\\"b&lt;c"' in dot and "digraph" in dot


def test_fallback_marks_the_root_cause_path_bold_red():
    from lib import lineage_component as lc

    dot = lc.fallback_dot(lc.payload(EDGES, NODES, TESTS, highlight=TEST, root_cause=STG))
    assert dot.count("penwidth=3") >= 2 and "#c62828" in dot


def test_use_component_needs_the_build_files(monkeypatch, tmp_path):
    from lib import lineage_component as lc

    assert lc.use_component() is True  # the build is committed
    monkeypatch.setattr(lc, "BUILD_DIR", tmp_path)
    assert lc.use_component() is False


# --- page logic ------------------------------------------------------------------------------------------------

def test_failed_test_options_and_pane_summary():
    from lib import lineage_component as lc
    from lib.pages import lineage as logic

    assert [o["id"] for o in logic.failed_test_options(TESTS)] == [TEST]
    s = logic.pane_summary(MART, lc.payload(EDGES, NODES, TESTS, highlight=None, root_cause=None))
    assert s["status"] == "ok" and s["label"] == "orders"
    assert s["upstream"] == 2 and s["downstream"] == 1 and s["tests_failing"] == 1
    assert logic.pane_summary("model.nope", {"nodes": [], "edges": []}) is None


# --- the page --------------------------------------------------------------------------------------------------

def _patch(monkeypatch, **reads):
    from lib import queries

    defaults = dict(lineage_latest=[], node_executions=[], test_results=[])
    defaults.update(reads)
    for name, rows in defaults.items():
        if isinstance(rows, Exception):
            def boom(*a, _e=rows, **k):
                raise _e
            monkeypatch.setattr(queries, name, boom)
        else:
            monkeypatch.setattr(queries, name, lambda *a, _r=rows, **k: list(_r))


def _run():
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_lineage_all_empty(monkeypatch):
    _patch(monkeypatch)
    at = _run()
    assert at.title[0].value == "Lineage" and at.info


def test_lineage_read_error_shows_caption(monkeypatch):
    _patch(monkeypatch, lineage_latest=RuntimeError("no session"))
    at = _run()
    assert at.info and any("no session" in c.value for c in at.caption)


def test_lineage_renders_with_failed_test_selector(monkeypatch):
    _patch(monkeypatch, lineage_latest=EDGES, node_executions=NODES, test_results=TESTS)
    at = _run()
    assert not at.info
    sel = at.selectbox(key="lineage_test")
    assert sel.options == ["None", "not_null_orders_id"]
    sel.select_index(1)
    at.run()
    assert not at.exception
    assert any("Root cause" in c.value and "stg_orders" in c.value for c in at.caption)
