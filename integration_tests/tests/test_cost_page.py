"""Offline tests of the Cost page logic, streamlit/lib/cost.py."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "streamlit"))
from lib import cost  # noqa: E402

NOW = datetime(2026, 11, 2, 12)


def test_credits_per_model_sums_and_ranks_and_skips_unknown():
    rows = [
        {"unique_id": "model.p.a", "name": "a", "type": "model", "credits": 0.5},
        {"unique_id": "model.p.a", "name": "a", "type": "model", "credits": 1.5},
        {"unique_id": "model.p.b", "name": "b", "type": "model", "credits": 3},
        {"unique_id": "model.p.c", "name": "c", "type": "model", "credits": 0},  # settled 0 counts
        {"unique_id": "model.p.d", "name": "d", "type": "model", "credits": None},  # not landed
    ]
    out = cost.credits_per_model(rows)
    assert [r["unique_id"] for r in out] == ["model.p.b", "model.p.a", "model.p.c"]
    a = out[1]
    assert (a["credits"], a["runs"], a["avg_credits"]) == (2.0, 2, 1.0)


def test_credits_per_run_newest_first_and_drops_uncosted():
    rows = [
        {"invocation_id": "i1", "started_at": NOW - timedelta(days=2), "credits": 1.0},
        {"invocation_id": "i2", "started_at": NOW, "credits": 2.0},
        {"invocation_id": "i3", "started_at": NOW - timedelta(days=1), "credits": None},
    ]
    assert [r["invocation_id"] for r in cost.credits_per_run(rows)] == ["i2", "i1"]


def test_daily_trend_is_30_zero_filled_days():
    rows = [
        {"started_at": NOW, "credits": 1.0}, {"started_at": NOW.replace(hour=1), "credits": 2.0},
        {"started_at": NOW - timedelta(days=29), "credits": 4.0},
        {"started_at": NOW - timedelta(days=40), "credits": 9.0},  # outside the window
        {"started_at": NOW, "credits": None},
    ]
    t = cost.daily_trend(rows, NOW)
    assert len(t) == 30
    assert t[0]["credits"] == 4.0 and t[-1]["credits"] == 3.0
    assert t[-1]["day"] == NOW.date() and sum(p["credits"] for p in t) == 7.0


def _m(relation, value, expected, anomaly=True, score=3.0, metric="credits"):
    return {"relation": relation, "metric": metric, "value": value, "anomaly_expected": expected,
            "anomaly_upper": None, "anomaly_score": score, "anomaly_is_anomaly": anomaly,
            "anomaly_engine": "z", "observed_at": NOW, "invocation_id": "i"}


def test_top_regressions_flagged_only_worst_first_and_limited():
    rows = [
        _m("A", 5, 1), _m("B", 10, 2), _m("C", 3, 2, anomaly=False), _m("D", 1, 4),  # D: a drop
        _m("E", 9, 1, metric="row_count"), _m("F", 4, 1, score=None),
    ]
    out = cost.top_regressions(rows)
    assert [r["relation"] for r in out] == ["B", "A", "F"]
    assert out[0]["excess"] == 8
    assert len(cost.top_regressions(rows, limit=1)) == 1


def test_regression_without_expected_uses_upper_bound():
    r = _m("A", 5, None)
    r["anomaly_upper"] = 2.0
    assert cost.top_regressions([r])[0]["excess"] == 3.0


def test_page_renders_trend_models_runs_and_regressions(monkeypatch):
    """The page with fixture rows from the query layer: a chart and the three tables, no exception."""
    pytest.importorskip("streamlit")  # not in the dbt venvs: see test_app_shell.py
    from streamlit.testing.v1 import AppTest

    from lib import queries

    now = datetime.now()
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "streamlit"))
    monkeypatch.setattr(queries, "invocations", lambda **k: [
        {"invocation_id": "i1", "command": "build", "target": "prod", "started_at": now, "status": "success",
         "credits": 2.0}])
    monkeypatch.setattr(queries, "node_executions", lambda **k: [
        {"unique_id": "model.p.a", "name": "a", "type": "model", "credits": 2.0}])
    monkeypatch.setattr(queries, "metrics", lambda **k: [_m("model.p.a", 5, 1)])
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "streamlit" / "pages" / "cost.py"),
                           default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert [s.value for s in at.subheader] == ["Trend, last 30 days", "Credits per model", "Credits per run",
                                              "Top cost regressions"]
    assert len(at.dataframe) == 3
