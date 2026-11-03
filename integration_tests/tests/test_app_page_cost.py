"""Cost page (Console task 10): logic in streamlit/lib/pages/cost.py and an AppTest render with the reads patched.

Needs streamlit (not in the dbt venv, so it skips there); run with --noconftest in the Streamlit 1.50 venv.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib.pages import cost as page  # noqa: E402

NOW = datetime(2026, 11, 2, 12)


def inv(days_ago, credits, i="i"):
    return {"invocation_id": f"{i}{days_ago}", "started_at": NOW - timedelta(days=days_ago), "credits": credits}


def node(uid, days_ago, credits, settled=None, name=None):
    return {"unique_id": uid, "name": name or uid.split(".")[-1], "type": "model", "credits": credits,
            "credits_settled": settled, "started_at": NOW - timedelta(days=days_ago, hours=1)}


def test_tiles_window_change_share_and_unattributed():
    invs = [inv(1, 3.0), inv(2, 1.0), inv(10, 2.0), inv(20, 5.0)]  # 7-day window: 4.0, previous 7 days: 2.0
    nodes = [node("model.p.a", 1, 3.0), node("model.p.b", 2, 1.0), node("model.p.c", 3, 0, "no_attribution"),
             node("model.p.d", 4, None)]
    t = {x.label: x for x in page.tiles(invs, nodes, now=NOW, days=7)}
    assert t["Credits in window"].value == "4.00"
    assert t["Change vs previous window"].value == "+100%"
    assert t["Top model share"].value.startswith("a") and "75%" in t["Top model share"].value
    assert t["Unattributed runs"].value == 1


def test_tiles_previous_window_zero_and_all_empty():
    t = {x.label: x for x in page.tiles([inv(1, 3.0)], [], now=NOW, days=7)}
    assert t["Change vs previous window"].value == "—"
    assert t["Top model share"].value is None
    e = {x.label: x for x in page.tiles([], [], now=NOW, days=7)}
    assert e["Credits in window"].value == "0.00" and e["Unattributed runs"].value == 0


def test_per_model_trend_has_15_points_and_settled_note():
    nodes = [node("model.p.a", 0, 2.0), node("model.p.a", 1, 1.0), node("model.p.a", 40, 9.0),
             node("model.p.s", 0, 0, "no_attribution"), node("model.p.n", 0, None)]
    rows = {r["unique_id"]: r for r in page.per_model(nodes, now=NOW)}
    a = rows["model.p.a"]
    assert len(a["trend"]) == page.TREND_POINTS == 15
    assert a["trend"][-1] == 2.0 and a["trend"][-2] == 1.0 and sum(a["trend"]) == 3.0  # 40 days ago is outside
    assert a["attribution"] == ""
    assert rows["model.p.s"]["attribution"] == "settled at 0"
    assert "model.p.n" not in rows  # no cost landed yet
    assert list(rows)[0] == "model.p.a"  # biggest first


def test_regressions_merge_metric_anomalies_and_cost_alerts():
    metrics = [
        {"relation": "db.s.a", "metric": "credits", "anomaly_is_anomaly": True, "value": 5.0, "anomaly_expected": 1.0,
         "observed_at": NOW, "node_unique_id": "model.p.a", "anomaly_engine": "native"},
        {"relation": "db.s.ok", "metric": "credits", "anomaly_is_anomaly": False, "value": 5.0, "observed_at": NOW},
    ]
    alerts = [
        {"incident_key": "k1", "kind": "cost", "node_key": "db.s.a|credits||", "title": "dup", "severity": "warn",
         "last_detected_at": NOW},
        {"incident_key": "k2", "kind": "cost", "node_key": "db.s.z|credits||", "title": "z spiked", "severity": "error",
         "last_detected_at": NOW},
        {"incident_key": "k3", "kind": "test_failure", "node_key": "test.p.t", "title": "no", "last_detected_at": NOW},
    ]
    out = page.regressions(metrics, alerts, [{"relation": "db.s.z", "unique_id": "model.p.z"}])
    assert [r["unique_id"] for r in out] == ["model.p.a", "model.p.z"]  # the alert for a is the metric's duplicate
    assert [r["relation"] for r in out] == ["db.s.a", "db.s.z"]
    assert page.regressions([], alerts[1:2])[0]["unique_id"] is None  # unknown relation: no click
    assert out[0]["excess"] == 4.0 and out[0]["source"] == "anomaly"
    assert out[1]["source"] == "alert" and out[1]["detail"] == "z spiked"


def test_regressions_empty():
    assert page.regressions([], []) == []


# --- AppTest ----------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))


def render(monkeypatch, *, invs=(), nodes=(), metrics=(), alerts=(), error=None):
    from lib import queries

    def read(rows):
        def f(*a, **k):
            if error:
                raise RuntimeError(error)
            return list(rows)
        return f

    monkeypatch.setattr(queries, "invocations", read(invs))
    monkeypatch.setattr(queries, "node_executions", read(nodes))
    monkeypatch.setattr(queries, "metrics", read(metrics))
    monkeypatch.setattr(queries, "alerts_feed", read(alerts))
    at = AppTest.from_file(str(APP_DIR / "pages" / "cost.py"), default_timeout=30)
    at.run()
    return at


def test_cost_all_empty(monkeypatch):
    at = render(monkeypatch)
    assert not at.exception
    assert at.title[0].value == "Cost" and at.info


def test_cost_read_error_shows_caption(monkeypatch):
    at = render(monkeypatch, error="boom")
    assert not at.exception
    assert any("boom" in c.value for c in at.caption)


def test_cost_renders_tiles_chart_and_tables(monkeypatch):
    now = datetime.now()
    invs = [{"invocation_id": "i1", "started_at": now - timedelta(days=1), "credits": 2.0}]
    nodes = [{"unique_id": "model.p.<b>x</b>", "name": "<b>x</b>", "type": "model", "credits": 2.0,
              "credits_settled": None, "started_at": now - timedelta(days=1)}]
    metrics = [{"relation": "r", "metric": "credits", "anomaly_is_anomaly": True, "value": 5.0,
                "anomaly_expected": 1.0, "observed_at": now, "node_unique_id": "model.p.<b>x</b>"}]
    at = render(monkeypatch, invs=invs, nodes=nodes, metrics=metrics)
    assert not at.exception
    assert [m.label for m in at.metric] == ["Credits in window", "Change vs previous window", "Top model share",
                                            "Unattributed runs"]
    assert len(at.dataframe) == 2


def test_per_model_window_matches_tiles():
    nodes = [node("model.p.a", 1, 2.0), node("model.p.a", 20, 9.0)]
    assert page.per_model(page.in_window(nodes, NOW, 7), now=NOW)[0]["credits"] == 2.0

