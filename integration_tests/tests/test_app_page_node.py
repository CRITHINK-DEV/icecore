"""Models & sources (the node page): pure logic of lib/pages/node.py, plus an AppTest render with the reads patched.

Needs streamlit, so it skips on the dbt venvs. Run it from the Streamlit 1.50 venv:

    cd integration_tests && python -m pytest --noconftest tests/test_app_page_node.py
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
PAGE = str(APP_DIR / "pages" / "node.py")
UID = "model.shop.orders"
REL = "SHOP.GOLD.ORDERS"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


def _node(day, status="success", credits=0.5, uid=UID, **kw):
    return {"invocation_id": f"inv{day}", "unique_id": uid, "type": "model", "name": uid.split(".")[-1],
            "materialization": "table", "relation": REL, "status": status, "message": None, "execution_time_s": 3.0,
            "started_at": f"2026-11-0{day} 06:00:00", "credits": credits, "layer": "gold", **kw}


NODES = [_node(1), _node(2, "error", 0.7), _node(3, "success", None),
         _node(3, "success", 0.1, uid="model.shop.customers"), {**_node(3, uid="source.shop.raw.orders"), "type": "source"}]
EDGES = [
    {"parent_unique_id": "model.shop.customers", "child_unique_id": UID},
    {"parent_unique_id": UID, "child_unique_id": "model.shop.report"},
    {"parent_unique_id": UID, "child_unique_id": "test.shop.not_null_orders_id.abc"},
]
TESTS = [
    {"test_unique_id": "test.shop.not_null_orders_id.abc", "test_name": "not_null", "status": "pass",
     "executed_at": "2026-11-03 06:01:00", "tested_unique_id": UID},
    {"test_unique_id": "test.shop.not_null_orders_id.abc", "test_name": "not_null", "status": "fail",
     "executed_at": "2026-11-02 06:01:00", "tested_unique_id": UID},
    {"test_unique_id": "test.shop.other", "test_name": "unique", "status": "fail",
     "executed_at": "2026-11-03 06:01:00", "tested_unique_id": "model.shop.customers"},
]
METRICS = [
    {"relation": REL, "metric": "row_count", "column": None, "observed_at": "2026-11-02 06:00:00", "value": 10},
    {"relation": REL, "metric": "row_count", "column": None, "observed_at": "2026-11-01 06:00:00", "value": 8},
    {"relation": "OTHER", "metric": "row_count", "column": None, "observed_at": "2026-11-01 06:00:00", "value": 1},
    {"relation": REL, "metric": "freshness_lag_seconds", "column": None, "observed_at": "2026-11-03 06:00:00",
     "value": 120},
]
CHANGES = [
    {"relation": REL, "change_kind": "column_added", "column": "tax", "detected_at": "2026-11-02 06:00:00",
     "node_unique_id": UID},
    {"relation": "OTHER", "change_kind": "column_removed", "column": "x", "detected_at": "2026-11-02 06:00:00",
     "node_unique_id": "model.shop.other"},
]
COLUMNS = [
    {"relation": REL, "column": "id", "data_type": "NUMBER", "snapshot_at": "2026-11-01 06:00:00", "is_new": False,
     "invocation_id": "inv1"},
    {"relation": REL, "column": "id", "data_type": "NUMBER", "snapshot_at": "2026-11-03 06:00:00", "is_new": False,
     "invocation_id": "inv3"},
    {"relation": REL, "column": "tax", "data_type": "FLOAT", "snapshot_at": "2026-11-03 06:00:00", "is_new": True,
     "invocation_id": "inv3"},
]


def summary(**over):
    from lib.pages import node

    args = dict(unique_id=UID, node_rows=NODES, test_results=TESTS, metrics=METRICS, schema_changes=CHANGES,
                lineage_rows=EDGES)
    args.update(over)
    return node.node_summary(**args)


def test_node_summary_status_history_newest_first():
    s = summary()
    assert s["found"] and s["name"] == "orders" and s["relation"] == REL and s["layer"] == "gold"
    assert [h["invocation_id"] for h in s["history"]] == ["inv3", "inv2", "inv1"]
    assert s["status"] == "ok" and s["last_run"] == "2026-11-03 06:00:00"


def test_node_summary_tests_use_tested_unique_id():
    s = summary()
    assert [t["test_unique_id"] for t in s["tests"]] == ["test.shop.not_null_orders_id.abc"]
    assert s["tests"][0]["status"] == "ok"  # the latest result wins
    assert (s["tests_total"], s["tests_passing"]) == (1, 1)


def test_node_summary_tests_fall_back_to_lineage_without_the_column():
    rows = [{k: v for k, v in t.items() if k != "tested_unique_id"} for t in TESTS]
    s = summary(test_results=rows)
    assert [t["test_unique_id"] for t in s["tests"]] == ["test.shop.not_null_orders_id.abc"]


def test_node_summary_metrics_series_and_freshness():
    s = summary()
    assert list(s["metric_series"]) == [("freshness_lag_seconds", None), ("row_count", None)]
    assert [p["value"] for p in s["metric_series"][("row_count", None)]] == [8, 10]  # oldest first, own relation only
    assert s["freshness_lag_s"] == 120


def test_node_summary_schema_history_and_columns():
    s = summary(schema_columns=COLUMNS)
    assert [c["column"] for c in s["schema_history"]] == ["tax"]
    assert [(c["column"], c["is_new"]) for c in s["columns"]] == [("id", False), ("tax", True)]  # latest snapshot only


def test_node_summary_cost_trend():
    s = summary()
    assert s["credits_total"] == pytest.approx(1.2)
    assert s["cost_by_day"] == [{"day": "2026-11-01", "credits": 0.5}, {"day": "2026-11-02", "credits": 0.7}]


def test_node_summary_neighbours_one_hop():
    s = summary()
    assert [n["unique_id"] for n in s["parents"]] == ["model.shop.customers"]
    assert sorted(n["unique_id"] for n in s["children"]) == ["model.shop.report", "test.shop.not_null_orders_id.abc"]
    assert s["parents"][0]["status"] == "ok"
    assert {n["unique_id"]: n["status"] for n in s["children"]}["model.shop.report"] == "not_run"


def test_node_summary_without_new_columns():
    nodes = [{k: v for k, v in n.items() if k not in ("layer", "credits")} for n in NODES]
    s = summary(node_rows=nodes)
    assert s["layer"] is None and s["credits_total"] is None and s["cost_by_day"] == []


def test_node_unknown_id():
    s = summary(unique_id="model.shop.nope")
    assert s["found"] is False and s["history"] == [] and s["tests"] == []


def test_node_summary_all_empty():
    from lib.pages import node

    s = node.node_summary(UID, [], [], [], [], [])
    assert s["found"] is False and s["status"] == "not_run" and s["parents"] == [] and s["columns"] == []


def test_node_only_in_lineage_is_found():
    s = summary(node_rows=[])
    assert s["found"] and s["status"] == "not_run" and s["relation"] is None


def test_neighbourhood_dot_escapes_and_marks_the_node():
    dot = summary(node_rows=NODES)
    from lib.pages import node

    text = node.neighbourhood_dot(dot)
    assert text.startswith("digraph") and "orders" in text and "model.shop.customers" in text
    assert "<script>" not in node.neighbourhood_dot({**dot, "name": '<script>"x"'})


# --- the page --------------------------------------------------------------------------------------------------

def _patch(monkeypatch, **reads):
    from lib import queries

    defaults = dict(lineage_latest=[], node_executions=[], test_results=[], metrics=[], schema_changes=[])
    defaults.update(reads)
    for name, rows in defaults.items():
        if isinstance(rows, Exception):
            def boom(*a, _e=rows, **k):
                raise _e
            monkeypatch.setattr(queries, name, boom)
        else:
            monkeypatch.setattr(queries, name, lambda *a, _r=rows, **k: list(_r))


def _run(node=None):
    at = AppTest.from_file(PAGE, default_timeout=30)
    if node:
        at.query_params["node"] = node
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_node_all_empty(monkeypatch):
    _patch(monkeypatch)
    at = _run()
    assert at.title[0].value == "Models & sources" and at.info


def test_node_read_error_shows_caption(monkeypatch):
    _patch(monkeypatch, lineage_latest=RuntimeError("no session"))
    at = _run(UID)
    assert at.info and any("no session" in c.value for c in at.caption)


def test_node_renders_with_a_picker_and_no_node(monkeypatch):
    _patch(monkeypatch, lineage_latest=EDGES, node_executions=NODES)
    at = _run()
    assert at.selectbox and UID in at.selectbox[0].options
    assert not at.info


def test_node_unknown_id_page(monkeypatch):
    _patch(monkeypatch, lineage_latest=EDGES, node_executions=NODES)
    at = _run("model.shop.nope")
    assert any("No node with this id" in i.value for i in at.info)
    assert at.selectbox  # the picker stays


def test_node_renders_a_known_node(monkeypatch):
    _patch(monkeypatch, lineage_latest=EDGES, node_executions=NODES, test_results=TESTS, metrics=METRICS,
           schema_changes=CHANGES)
    at = _run(UID)
    assert at.query_params["node"] == [UID] or at.query_params["node"] == UID
    assert [m.label for m in at.metric][:3] == ["Status", "Tests passing", "Last run"]
    assert not at.info


def test_read_schema_columns_binds_the_relation():
    from lib import queries
    from lib.pages import node

    seen = []

    class Layer(queries.QueryLayer):
        def run(self, built):
            seen.append(built)
            return []

    node.read_schema_columns("A.B.C; drop", layer=Layer(None, "OBS"))
    sql, params = seen[0]
    assert "OBS.SCHEMA_COLUMNS" in sql and "drop" not in sql and params[0] == "A.B.C; drop"
    with pytest.raises(ValueError):
        node.read_schema_columns("x", layer=Layer(None, "bad name"))
