"""Console domain logic: health score, incident state, "as of" (pure, no Streamlit needed).

    cd integration_tests && python -m pytest --noconftest tests/test_app_domain.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
NOW = datetime(2026, 11, 3, 12, 0, 0)


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name)


def _inc(kind, node_key, **kw):
    return {"kind": kind, "node_key": node_key, "first_detected_at": NOW - timedelta(days=1),
            "last_detected_at": NOW - timedelta(hours=5), "acknowledged_at": None, **kw}


def _state(inc, **kw):
    from lib import incidents
    args = {"test_results": [], "node_executions": [], "metrics": [], "now": NOW}
    args.update(kw)
    return incidents.state(inc, **args)


def test_score_weights_and_renormalisation():
    from lib import health
    got = health.score({"tests": 100, "freshness": None, "schedules": 0, "anomalies": None})
    assert got == round(100 * 0.5 / 0.65, 1)
    assert health.score({"tests": None, "freshness": None, "schedules": None, "anomalies": None}) is None
    assert health.WEIGHTS == {"tests": 0.5, "freshness": 0.2, "schedules": 0.15, "anomalies": 0.15}


def test_components_from_statuses():
    from lib import health
    c = health.components(["pass", "warn", "fail"], [True, False], [], [True, False, False, False])
    assert c["tests"] == 50.0
    assert c["freshness"] == 50.0
    assert c["schedules"] is None
    assert c["anomalies"] == 75.0
    assert health.components([], [], [], [])["tests"] is None
    assert health.components(["error", "success"], [], [], [])["tests"] == 50.0


def test_formula_help_shows_weights():
    from lib import health
    text = health.formula_help()
    for w in ("0.5", "0.2", "0.15"):
        assert w in text


def test_state_per_kind():
    t = NOW - timedelta(hours=1)
    # test_failure: a later pass resolves; an earlier one does not
    inc = _inc("test_failure", "test.a")
    assert _state(inc) == "open"
    assert _state(inc, test_results=[{"test_unique_id": "test.a", "status": "pass", "executed_at": t}]) == "resolved"
    assert _state(inc, test_results=[{"test_unique_id": "test.a", "status": "pass", "executed_at": NOW - timedelta(days=2)}]) == "open"
    assert _state(inc, test_results=[{"test_unique_id": "test.a", "status": "fail", "executed_at": t}]) == "open"
    # silence: node success started after first_detected_at
    inc = _inc("silence", "model.m")
    assert _state(inc, node_executions=[{"unique_id": "model.m", "status": "success", "started_at": t}]) == "resolved"
    assert _state(inc, node_executions=[{"unique_id": "model.m", "status": "error", "started_at": t}]) == "open"
    assert _state(inc, node_executions=[{"unique_id": "model.m", "status": "success", "started_at": NOW - timedelta(days=3)}]) == "open"
    # anomaly and cost: next point of the same series after last_detected_at is inside its band
    for kind in ("anomaly", "cost"):
        inc = _inc(kind, "db.s.t|row_count|c|d")
        pt = {"relation": "db.s.t", "metric": "row_count", "column": "c", "dimension": "d", "observed_at": t,
              "anomaly_is_anomaly": False}
        assert _state(inc, metrics=[pt]) == "resolved"
        assert _state(inc, metrics=[{**pt, "anomaly_is_anomaly": True}]) == "open"
        assert _state(inc, metrics=[{**pt, "metric": "other"}]) == "open"
        assert _state(inc, metrics=[{**pt, "observed_at": NOW - timedelta(days=2)}]) == "open"
    # schema_change: only when acknowledged
    inc = _inc("schema_change", "db.s.t|c")
    assert _state(inc) == "open"
    assert _state(inc, now=NOW) == "open"
    assert _state({**inc, "acknowledged_at": t}) == "resolved"
    # acknowledged for other kinds; resolved beats acknowledged
    inc = _inc("test_failure", "test.a", acknowledged_at=t)
    assert _state(inc) == "acknowledged"
    assert _state(inc, test_results=[{"test_unique_id": "test.a", "status": "pass", "executed_at": t}]) == "resolved"
    # stale: open and first seen more than 7 days ago
    old = _inc("test_failure", "test.a", first_detected_at=NOW - timedelta(days=8))
    assert _state(old) == "stale"
    assert _state({**old, "acknowledged_at": t}) == "acknowledged"


def test_incident_age_never_negative():
    from lib import incidents
    assert incidents.age(NOW + timedelta(minutes=5), NOW) == timedelta(0)
    assert incidents.age(NOW - timedelta(hours=2), NOW) == timedelta(hours=2)


def test_as_of_is_latest_record():
    from lib import incidents
    a = [{"executed_at": datetime(2026, 11, 1)}, {"executed_at": datetime(2026, 11, 2)}]
    b = [{"detected_at": datetime(2026, 11, 3, 9)}, {"other": datetime(2030, 1, 1)}]
    assert incidents.as_of(a, b) == datetime(2026, 11, 3, 9)
    assert incidents.as_of([], [{}]) is None
    assert incidents.as_of() is None


def test_state_without_new_columns():
    from lib import incidents
    inc = {"kind": "test_failure", "node_key": "test.a", "first_detected_at": NOW - timedelta(hours=1)}
    got = incidents.state(inc, test_results=[{"test_unique_id": "test.a", "status": "pass"}],
                          node_executions=[{}], metrics=[{}], now=NOW)
    assert got in {"open", "resolved"}
    assert incidents.state({"kind": "anomaly", "node_key": "x"}, test_results=[], node_executions=[], metrics=[], now=NOW) == "open"
    assert incidents.state({}, test_results=[], node_executions=[], metrics=[], now=NOW) == "open"


def test_summary():
    from lib import incidents
    sev = lambda s: {"severity": s, "first_detected_at": NOW - timedelta(hours=3)}
    states = [(sev("error"), "open"), (sev("warn"), "open"), (sev("error"), "acknowledged"),
              ({"severity": "error", "first_detected_at": NOW - timedelta(days=9)}, "stale"),
              (sev("error"), "resolved")]
    s = incidents.summary(states)
    assert s["open"] == 2 and s["acknowledged"] == 1 and s["errors_open"] == 1 and s["stale"] == 1
    assert s["oldest_open"] == NOW - timedelta(hours=3)
    empty = incidents.summary([])
    assert empty["open"] == 0 and empty["oldest_open"] is None
