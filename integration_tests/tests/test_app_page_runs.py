"""Runs page (Console T7): logic and an AppTest render with the reads patched. Needs streamlit 1.50 (skips on dbt venvs)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
PAGE = str(APP_DIR / "pages" / "runs.py")

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
RUNS = [
    {"invocation_id": "a1", "project": "p", "target": "prod", "status": "success", "command": "build",
     "started_at": NOW - timedelta(hours=3), "duration_s": 60, "credits": 0.5, "nodes_failed": 0, "nodes_total": 3},
    {"invocation_id": "b2", "project": "p", "target": "prod", "status": "error", "command": "run",
     "started_at": NOW - timedelta(hours=1), "duration_s": 30, "credits": None, "nodes_failed": 1, "nodes_total": 2},
]
NODES = [
    {"unique_id": f"model.p.n{i}", "name": f"n{i}", "status": "success", "execution_time_s": i + 1,
     "started_at": NOW - timedelta(hours=1, seconds=100 - i), "credits": None, "rows_affected": 5}
    for i in range(12)
] + [{"unique_id": "model.p.skipped", "name": "skipped", "status": "skipped", "execution_time_s": None,
      "started_at": None}]


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_tiles_runs_failed_median_credits():
    from lib.pages import runs as logic

    t = {x.label: x for x in logic.tiles(RUNS)}
    assert t["Runs"].value == 2 and t["Failed"].value == 1
    assert t["Median duration"].value == "45 s"
    assert t["Credits"].value == "0.500" and t["Credits"].delta == "1 pending"


def test_credits_pending_while_null():
    from lib.pages import runs as logic

    only_null = [r for r in RUNS if r["credits"] is None]
    assert {x.label: x for x in logic.tiles(only_null)}["Credits"].value == "pending"
    assert logic.runs_table(only_null)[0]["credits"] == "pending"


def test_runs_all_empty():
    from lib.pages import runs as logic

    t = {x.label: x for x in logic.tiles([])}
    assert t["Runs"].value == 0 and t["Failed"].value == 0
    assert t["Median duration"].value is None and t["Credits"].value is None
    assert logic.runs_table([]) == [] and logic.slowest_nodes([]) == []
    assert logic.selected_run([], "a1") is None


def test_runs_table_newest_first():
    from lib.pages import runs as logic

    assert [r["invocation_id"] for r in logic.runs_table(RUNS)] == ["b2", "a1"]


def test_selected_run_param_or_newest():
    from lib.pages import runs as logic

    assert logic.selected_run(RUNS, "a1")["invocation_id"] == "a1"
    assert logic.selected_run(RUNS, "nope")["invocation_id"] == "b2"
    assert logic.selected_run(RUNS, None)["invocation_id"] == "b2"


def test_slowest_nodes_top_10_with_unique_id():
    from lib.pages import runs as logic

    top = logic.slowest_nodes(NODES)
    assert len(top) == 10 and top[0]["name"] == "n11" and top[0]["unique_id"] == "model.p.n11"
    assert all(r["name"] != "skipped" for r in top)


def test_filter_by_status():
    from lib.pages import runs as logic

    assert [r["invocation_id"] for r in logic.filter_by_status(RUNS, ["failed"])] == ["b2"]
    assert len(logic.filter_by_status(RUNS, [])) == 2


def _patch(monkeypatch, runs, nodes, boom=False):
    from lib import queries

    def inv(*a, **k):
        if boom:
            raise RuntimeError("no session")
        return runs

    monkeypatch.setattr(queries, "invocations", inv)
    monkeypatch.setattr(queries, "node_executions", lambda *a, **k: nodes)


def _run():
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    return at


def test_runs_read_error_shows_caption(monkeypatch):
    _patch(monkeypatch, [], [], boom=True)
    at = _run()
    assert not at.exception
    assert any("no session" in c.value for c in at.caption)
    assert not at.metric


def test_runs_page_empty_window(monkeypatch):
    _patch(monkeypatch, [], [])
    at = _run()
    assert not at.exception and not at.metric


def test_runs_page_renders_tiles_and_selected_run(monkeypatch):
    _patch(monkeypatch, RUNS, NODES)
    at = _run()
    assert not at.exception, [e.value for e in at.exception]
    assert [m.label for m in at.metric] == ["Runs", "Failed", "Median duration", "Credits"]
    assert [m.value for m in at.metric][:2] == ["2", "1"]
    assert any("hours after" in c.value for c in at.caption)
    at.query_params["run"] = "a1"
    at.run()
    assert not at.exception
