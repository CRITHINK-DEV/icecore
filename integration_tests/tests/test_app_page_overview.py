"""Overview page: pure logic (lib/pages/overview.py) and an AppTest render with the reads patched.

    cd integration_tests && $SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_page_overview.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
pytest.importorskip("altair")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
PAGE = str(APP_DIR / "pages" / "overview.py")
NOW = datetime(2026, 11, 3, 12, 0, 0)


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setenv("ICECORE_VIEWS_SCHEMA", "ICECORE_OBS.X")


def ov():
    from lib.pages import overview
    return overview


def inc(key, kind="test_failure", severity="error", first=NOW - timedelta(days=1), **kw):
    return {"incident_key": key, "kind": kind, "node_key": kw.pop("node_key", "test.p.t1"), "severity": severity,
            "title": kw.pop("title", f"title {key}"), "first_detected_at": first, "last_detected_at": first,
            "acknowledged_at": None, **kw}


def test_with_states_adds_state():
    rows = ov().with_states([inc("a"), inc("b", first=NOW - timedelta(days=9))], test_results=[], node_executions=[],
                            metrics=[], now=NOW)
    assert [r["state"] for r in rows] == ["open", "stale"]


def test_needs_attention_open_only_errors_first_then_first_seen():
    rows = [
        {**inc("w1", severity="warn", first=NOW - timedelta(days=3)), "state": "open"},
        {**inc("e2", first=NOW - timedelta(hours=2)), "state": "open"},
        {**inc("e1", first=NOW - timedelta(days=2)), "state": "open"},
        {**inc("ack"), "state": "acknowledged"},
        {**inc("old"), "state": "stale"},
    ]
    out = ov().needs_attention(rows, now=NOW)
    assert [r["incident_key"] for r in out] == ["e1", "e2", "w1"]
    assert out[0]["status"] == "failed" and out[2]["status"] == "warning"
    assert out[0]["unique_id"] == "test.p.t1"
    assert out[0]["age"] == "2d"


def test_needs_attention_unique_id_only_for_node_kinds():
    out = ov().needs_attention([{**inc("a", kind="anomaly", node_key="REL|row_count||"), "state": "open"}], now=NOW)
    assert out[0]["unique_id"] is None


def test_tiles_values():
    incidents = [{**inc("a"), "state": "open"}, {**inc("b"), "state": "stale"}, {**inc("c"), "state": "resolved"}]
    runs = [
        {"invocation_id": "r1", "status": "success", "started_at": NOW - timedelta(days=1), "credits": 0.5},
        {"invocation_id": "r2", "status": "error", "started_at": NOW - timedelta(days=1), "credits": 0.25},
        {"invocation_id": "r3", "status": "success", "started_at": NOW - timedelta(days=2), "credits": None},
    ]
    tests = [
        {"test_unique_id": "t1", "status": "fail", "executed_at": NOW - timedelta(hours=3)},
        {"test_unique_id": "t1", "status": "pass", "executed_at": NOW - timedelta(hours=9)},
        {"test_unique_id": "t2", "status": "pass", "executed_at": NOW - timedelta(hours=1)},
        {"test_unique_id": "t3", "status": "error", "executed_at": NOW - timedelta(hours=1)},
    ]
    t = {x.label: x for x in ov().tiles(incidents, runs, tests, [{"unique_id": "m1"}, {"unique_id": "m2"}],
                                          [{"unique_id": "m1"}, {"unique_id": "m1"}], runs, now=NOW)}
    assert t["Open incidents"].value == 1 and "1 stale" in t["Open incidents"].delta
    assert t["Failing tests"].value == 2
    assert t["Successful runs"].value == "2 of 3"
    assert t["Missed schedules"].value == 1 and "2 declared" in t["Missed schedules"].delta
    assert t["Credits"].value == "0.75"


def test_overview_all_empty():
    o = ov()
    t = {x.label: x for x in o.tiles([], [], [], [], [], [], now=NOW)}
    assert t["Open incidents"].value == 0 and t["Failing tests"].value == 0
    assert t["Successful runs"].value == "0 of 0"
    assert t["Missed schedules"].value is None and t["Credits"].value is None
    assert o.needs_attention([], now=NOW) == [] and o.nodes_not_ok([], []) == []
    assert o.layer_health([], [], [], [], [], []) is None


def test_nodes_not_ok_latest_per_node_not_ok_only():
    nodes = [
        {"unique_id": "m1", "name": "m1", "type": "model", "status": "error", "started_at": NOW, "layer": "silver",
         "message": "boom"},
        {"unique_id": "m1", "name": "m1", "type": "model", "status": "success", "started_at": NOW - timedelta(days=1)},
        {"unique_id": "m2", "name": "m2", "type": "model", "status": "success", "started_at": NOW},
        {"unique_id": "m3", "name": "m3", "type": "model", "status": "success", "started_at": NOW - timedelta(days=1)},
        {"unique_id": "m3", "name": "m3", "type": "model", "status": "skipped", "started_at": NOW},
    ]
    tests = [{"test_unique_id": "t9", "test_name": "t9", "status": "warn", "executed_at": NOW, "message": "w"}]
    out = ov().nodes_not_ok(nodes, tests)
    assert [(r["unique_id"], r["status"]) for r in out] == [("m1", "failed"), ("t9", "warning"), ("m3", "not_run")]
    assert out[0]["layer"] == "silver" and out[1]["type"] == "test"


def layer_inputs():
    """As the views write them: models carry a layer, tests do not; a test's layer comes from the node it tests."""
    nodes = [
        {"unique_id": "model.p.m1", "layer": "bronze", "status": "success", "started_at": NOW, "type": "model"},
        {"unique_id": "model.p.m2", "layer": "silver", "status": "success", "started_at": NOW, "type": "model"},
        {"unique_id": "test.p.t1", "layer": None, "status": "fail", "started_at": NOW, "type": "test"},
    ]
    tests = [{"test_unique_id": "test.p.t1", "tested_unique_id": "model.p.m2", "status": "fail", "executed_at": NOW},
             {"test_unique_id": "test.p.t2", "status": "pass", "executed_at": NOW}]
    lineage = [{"parent_unique_id": "model.p.m1", "child_unique_id": "test.p.t2", "parent_layer": "bronze",
                "child_layer": None}]
    return nodes, lineage, tests


def test_layer_health_scores_per_layer():
    nodes, lineage, tests = layer_inputs()
    out = ov().layer_health(nodes, lineage, tests, [], [{"unique_id": "model.p.m2"}], [{"unique_id": "model.p.m2"}])
    by = {r["layer"]: r for r in out}
    assert [r["layer"] for r in out] == ["bronze", "silver"]
    assert by["bronze"]["components"]["tests"] == 100.0  # t2 has no tested id: its LINEAGE parent m1 is bronze
    assert by["silver"]["components"]["tests"] == 0.0 and by["silver"]["components"]["schedules"] == 0.0
    assert by["silver"]["score"] == 0.0


def test_layer_health_freshness_and_anomalies_from_test_metrics():
    nodes, lineage, tests = layer_inputs()
    metrics = [  # written by tests: node_unique_id is the TEST's id
        {"relation": "R", "metric": "freshness_lag_seconds", "node_unique_id": "test.p.t2", "value": 50,
         "threshold": 100, "observed_at": NOW, "anomaly_engine": None},
        {"relation": "R2", "metric": "row_count", "node_unique_id": "test.p.t1", "value": 5,
         "anomaly_engine": "native", "anomaly_is_anomaly": True, "observed_at": NOW},
    ]
    by = {r["layer"]: r for r in ov().layer_health(nodes, lineage, tests, metrics, [], [])}
    assert by["bronze"]["components"]["freshness"] == 100.0
    assert by["silver"]["components"]["anomalies"] == 0.0


def test_overview_without_new_columns():
    """Rows from an older view (no layer anywhere): the strip is hidden, nothing raises."""
    nodes = [{"unique_id": "m1", "status": "success", "started_at": NOW}]
    assert ov().layer_health(nodes, [{"parent_unique_id": "a", "child_unique_id": "b"}], [], [], [], []) is None
    assert ov().layers_present(nodes, []) == []


def test_layers_present_orders_bronze_silver_gold_first():
    nodes = [{"unique_id": u, "layer": l} for u, l in (("a", "gold"), ("b", "seed"), ("c", "bronze"), ("d", "silver"))]
    assert ov().layers_present(nodes, []) == ["bronze", "silver", "gold", "seed"]


def test_runs_for_chart_adds_failing_tests_per_run():
    runs = [{"invocation_id": "r1", "status": "success", "started_at": NOW, "duration_s": 3}]
    tests = [{"invocation_id": "r1", "status": "fail"}, {"invocation_id": "r1", "status": "pass"},
             {"invocation_id": "r1", "status": "error"}]
    assert ov().runs_for_chart(runs, tests)[0]["tests_failed"] == 2


def test_window_rows_filters_by_days_back_from_now():
    rows = [{"started_at": NOW - timedelta(days=3)}, {"started_at": NOW - timedelta(days=10)}, {"started_at": None}]
    assert len(ov().in_window(rows, "started_at", NOW, 7)) == 1


# --- the page, with the reads patched -------------------------------------------------------------------------------

def render(monkeypatch, **reads):
    from lib import queries
    for name in ("alerts_feed", "invocations", "test_results", "node_executions", "metrics", "lineage_latest",
                 "sla_declared", "silence_events"):
        data = reads.get(name, [])
        if isinstance(data, Exception):
            def boom(*a, _e=data, **k):
                raise _e
            monkeypatch.setattr(queries, name, boom)
        else:
            monkeypatch.setattr(queries, name, lambda *a, _d=data, **k: _d)
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_overview_all_empty_page(monkeypatch):
    at = render(monkeypatch)
    assert at.title[0].value == "Overview"
    assert at.info


def test_overview_read_error_shows_caption(monkeypatch):
    at = render(monkeypatch, invocations=RuntimeError("no session"), alerts_feed=RuntimeError("no session"))
    assert at.info
    assert any("no session" in c.value for c in at.caption)


def test_overview_renders_with_rows(monkeypatch):
    from datetime import timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    runs = [{"invocation_id": "r1", "status": "success", "started_at": now - timedelta(hours=1), "duration_s": 12,
             "credits": 0.5}]
    incidents = [inc("a", first=now - timedelta(hours=2), title="<b>x</b>")]
    nodes = [{"unique_id": "m1", "name": "m1", "type": "model", "status": "error", "started_at": now,
              "layer": "silver", "message": "boom", "invocation_id": "r1"},
             {"unique_id": "m2", "name": "m2", "type": "model", "status": "success", "started_at": now,
              "layer": "gold", "invocation_id": "r1"}]
    at = render(monkeypatch, invocations=runs, alerts_feed=incidents, node_executions=nodes)
    labels = [m.label for m in at.metric]
    for want in ("Open incidents", "Failing tests", "Successful runs", "Missed schedules", "Credits"):
        assert want in labels
    # layers with no scorable data get no tile, one compact caption line instead
    assert not [m for m in at.metric if m.label.endswith("health")]
    assert any("No health score yet" in c.value and "silver" in c.value and "gold" in c.value for c in at.caption)
    assert len(at.dataframe) == 2
