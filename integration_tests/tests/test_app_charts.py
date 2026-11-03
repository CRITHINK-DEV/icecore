"""Chart library of the Console (Altair specs). Needs altair, so it skips where altair is missing.

    cd integration_tests && python -m pytest --noconftest tests/test_app_charts.py
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("altair")

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name)


def _charts():
    from lib import charts
    return charts


def _title(spec):
    t = spec.get("title")
    return t.get("text") if isinstance(t, dict) else t


def _axis_titles(spec):
    out = {}
    for ch in ("x", "y"):
        enc = spec["encoding"].get(ch)
        if enc:
            out[ch] = enc.get("title") or enc.get("axis", {}).get("title")
    return out


METRICS = [
    {"observed_at": "2026-11-01T00:00:00", "value": 10, "anomaly_expected": 10, "anomaly_lower": 8, "anomaly_upper": 12, "anomaly_is_anomaly": False},
    {"observed_at": "2026-11-02T00:00:00", "value": 11, "anomaly_expected": 10, "anomaly_lower": 8, "anomaly_upper": 12, "anomaly_is_anomaly": False},
    {"observed_at": "2026-11-03T00:00:00", "value": 30, "anomaly_expected": 10, "anomaly_lower": 8, "anomaly_upper": 12, "anomaly_is_anomaly": True},
]
RUNS = [
    {"invocation_id": "a", "started_at": "2026-11-01T01:00:00", "duration_s": 60, "status": "success", "nodes_failed": 0},
    {"invocation_id": "b", "started_at": "2026-11-02T01:00:00", "duration_s": 90, "status": "error", "nodes_failed": 3},
    {"invocation_id": "c", "started_at": "2026-11-03T01:00:00", "duration_s": 70, "status": "warn", "nodes_failed": 1},
]
NODES = [
    {"name": "b", "unique_id": "model.p.b", "status": "success", "started_at": "2026-11-03T01:00:05", "execution_time_s": 4},
    {"name": "a", "unique_id": "model.p.a", "status": "error", "started_at": "2026-11-03T01:00:00", "execution_time_s": 5},
    {"name": "c", "unique_id": "model.p.c", "status": "skipped", "started_at": None, "execution_time_s": 0},
]
SOURCES = [
    {"name": "src1", "lag_minutes": 30, "threshold": 60},
    {"name": "src2", "lag_minutes": 200, "threshold": 120},
    {"name": "src3", "lag_minutes": 5},
]


def test_band_chart_titles_layers_and_anomaly_filter():
    spec = _charts().band_chart(METRICS, title="Credits", unit="credits", engine="zscore").to_dict()
    assert len(spec["layer"]) == 4
    assert "Credits" in _title(spec)
    assert "zscore" in json.dumps(spec["title"])
    value = spec["layer"][2]
    assert "credits" in value["encoding"]["y"]["title"]
    assert "UTC" in value["encoding"]["x"]["title"]
    point = spec["layer"][3]
    assert "anomaly_is_anomaly" in json.dumps(point["transform"])
    assert point["mark"]["color"] == "#c62828" or point["encoding"]["color"]


def test_band_chart_accepts_plain_band_columns():
    rows = [{"observed_at": "2026-11-01T00:00:00", "value": 1, "expected": 1, "lower": 0, "upper": 2, "anomaly_is_anomaly": False}]
    spec = _charts().band_chart(rows, title="t", unit="rows").to_dict()
    assert len(spec["layer"]) == 4
    assert "lower" in json.dumps(spec["layer"][0]["encoding"]["y"])


def test_runs_chart_titles_status_colours_and_right_axis():
    spec = _charts().runs_chart(RUNS).to_dict()
    assert len(spec["layer"]) == 2
    bars, line = spec["layer"]
    assert bars["encoding"]["y"]["title"] == "Duration (seconds)"
    assert "UTC" in bars["encoding"]["x"]["title"]
    assert bars["encoding"]["color"]["scale"]["range"] == [
        "#c62828", "#b26a00", "#2e7d32", "#6a4fb3", "#9e9e9e"]
    assert line["encoding"]["y"]["axis"]["orient"] == "right"
    assert line["encoding"]["y"]["title"] == "Failing tests (count)"
    assert spec["resolve"]["scale"]["y"] == "independent"
    assert _title(spec)


def test_gantt_titles_sort_and_extent():
    chart = _charts().gantt(NODES)
    spec = chart.to_dict()
    assert spec["mark"]["type"] == "bar" or spec["mark"] == "bar"
    assert "UTC" in spec["encoding"]["x"]["title"]
    assert spec["encoding"]["x"]["field"] == "started_at"
    assert spec["encoding"]["x2"]["field"] == "ended_at"
    assert spec["encoding"]["y"]["title"] == "Node"
    assert _title(spec)
    data = spec["data"]["values"]
    assert [d["name"] for d in data] == ["a", "b"]  # sorted by start, nodes without a start left out
    assert data[0]["ended_at"].startswith("2026-11-03T01:00:05")


def test_daily_titles_and_colour():
    rows = [{"day": "2026-11-01", "credits": 1.5, "kind": "model"}, {"day": "2026-11-02", "credits": 2, "kind": "test"}]
    spec = _charts().daily(rows, x="day", y="credits", title="Credits per day", y_title="Credits", color="kind").to_dict()
    assert _title(spec) == "Credits per day"
    assert spec["encoding"]["y"]["title"] == "Credits"
    assert "UTC" in spec["encoding"]["x"]["title"]
    assert spec["encoding"]["color"]["field"] == "kind"


def test_daily_without_colour():
    spec = _charts().daily([{"d": "2026-11-01", "n": 1}], x="d", y="n", title="T", y_title="Y").to_dict()
    assert "color" not in spec["encoding"] or spec["encoding"]["color"].get("value")


def test_lag_vs_limit_layers_and_titles():
    spec = _charts().lag_vs_limit(SOURCES).to_dict()
    assert len(spec["layer"]) == 2
    bars, tick = spec["layer"]
    assert "minutes" in bars["encoding"]["x"]["title"] or "minutes" in bars["encoding"]["y"]["title"]
    assert "threshold" in json.dumps(tick["encoding"])
    assert _title(spec)
    data = spec["data"]["values"]
    assert len(data) == 3


def test_charts_accept_empty_rows():
    c = _charts()
    assert c.band_chart([], title="t", unit="u").to_dict()
    assert c.runs_chart([]).to_dict()
    assert c.gantt([]).to_dict()
    assert c.daily([], x="d", y="n", title="t", y_title="y").to_dict()
    assert c.lag_vs_limit([]).to_dict()
