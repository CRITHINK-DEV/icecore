"""Runs page logic and timeline chart data, offline (pure Python, no Streamlit). Page render needs streamlit (AppTest)."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import charts, queries, runs_logic as logic  # noqa: E402

RUNS = [
    {"invocation_id": "a1", "project": "p", "target": "prod", "status": "success", "command": "build",
     "started_at": datetime(2026, 11, 2, 10), "duration_s": 60, "credits": 0.5, "nodes_failed": 0, "nodes_total": 3},
    {"invocation_id": "b2", "project": "p", "target": "dev", "status": "error", "command": "run",
     "started_at": datetime(2026, 11, 2, 9), "duration_s": 30, "credits": None, "nodes_failed": 1, "nodes_total": 2},
]
NODES = [
    {"unique_id": "m.b", "name": "b", "status": "success", "started_at": datetime(2026, 11, 2, 10, 0, 5),
     "completed_at": datetime(2026, 11, 2, 10, 0, 9), "execution_time_s": 4, "credits": 0.2},
    {"unique_id": "m.a", "name": "a", "status": "success", "started_at": datetime(2026, 11, 2, 10, 0, 0),
     "completed_at": None, "execution_time_s": 3.5, "credits": None},
    {"unique_id": "m.c", "name": "c", "status": "skipped", "started_at": None, "completed_at": None,
     "execution_time_s": None},
]


class FakeSession:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def sql(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def collect(self):
        return self.rows


def test_run_list_has_status_duration_credits():
    rows = logic.run_table(RUNS)
    assert [(r["status"], r["duration_s"], r["credits"]) for r in rows] == [("success", 60, 0.5), ("error", 30, None)]


def test_run_list_reads_invocations_through_the_query_layer():
    session = FakeSession(RUNS)
    layer = queries.QueryLayer(session, "DB.ICECORE_OBS")
    rows = queries.invocations(since=logic.since_for("Last 7 days"), layer=layer)
    assert len(rows) == 2 and "DB.ICECORE_OBS.INVOCATIONS" in session.calls[0][0]


def test_filters_and_totals():
    assert [r["invocation_id"] for r in logic.filter_runs(RUNS, target="dev")] == ["b2"]
    assert [r["invocation_id"] for r in logic.filter_runs(RUNS, statuses=["success"])] == ["a1"]
    assert logic.distinct(RUNS, "project") == ["p"]
    assert logic.totals(RUNS) == {"runs": 2, "failed": 1, "credits": 0.5, "total_duration_s": 90.0}


def test_timeline_bars_offsets_and_durations():
    bars = charts.timeline_bars(NODES)
    assert [b["name"] for b in bars] == ["a", "b"]  # unstarted node left out, ordered by start
    assert (bars[0]["start_s"], bars[0]["end_s"]) == (0, 3.5)  # falls back to execution_time_s
    assert (bars[1]["start_s"], bars[1]["end_s"], bars[1]["duration_s"]) == (5, 9, 4)


def test_timeline_empty():
    assert charts.timeline_bars([]) == [] and charts.timeline_bars([NODES[2]]) == []


def test_slowest_nodes_order():
    assert [r["name"] for r in logic.slowest_nodes(NODES)] == ["b", "a"]


def test_empty_state_explains_how_to_enable_telemetry():
    assert "on-run-end" in logic.ENABLE_TELEMETRY_HELP and "icecore.setup" in logic.ENABLE_TELEMETRY_HELP
    assert logic.filter_runs([], project="p") == []


def test_page_render_with_and_without_runs(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.syspath_prepend(str(APP_DIR))
    for rows, expect_info in ((RUNS, False), ([], True)):
        monkeypatch.setattr(queries, "invocations", lambda *a, _r=rows, **k: _r)
        monkeypatch.setattr(queries, "node_executions", lambda *a, **k: NODES)
        at = AppTest.from_file(str(APP_DIR / "pages" / "runs.py"), default_timeout=30).run()
        assert not at.exception
        assert bool(at.info) is expect_info
        if not expect_info:
            assert len(at.dataframe) >= 1
