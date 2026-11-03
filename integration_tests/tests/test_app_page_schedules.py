"""Schedules & freshness page: logic (lib/pages/schedules.py, pure) and an AppTest render with the reads patched.

Everything here needs Streamlit (Tile lives in lib.console), so it skips in the dbt venvs:

    cd integration_tests && $SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_page_schedules.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("streamlit")  # lib.console (Tile) imports it

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import slas  # noqa: E402
from lib.pages import schedules as logic  # noqa: E402

T0 = datetime(2026, 11, 1, 8, 0, 0)


def decl(uid="model.p.orders", every=60, grace=5):
    return {"unique_id": uid, "expected_every_minutes": every, "grace_minutes": grace, "channel": "data",
            "severity": "warn"}


def run(uid, minutes, status="success"):
    return {"unique_id": uid, "status": status, "started_at": T0 + timedelta(minutes=minutes)}


def silence(uid="model.p.orders", detected=T0 + timedelta(hours=5)):
    return {"unique_id": uid, "expected_at": T0 - timedelta(hours=1), "grace": 5, "expected_every_minutes": 60,
            "last_success_at": T0 - timedelta(hours=3), "detected_at": detected, "channel": "data",
            "severity": "warn"}


def lag(relation, seconds, uid=None, threshold="absent", at=T0):
    row = {"relation": relation, "metric": "freshness_lag_seconds", "value": seconds, "observed_at": at,
           "node_unique_id": uid}
    if threshold != "absent":
        row["threshold"] = threshold
    return row


# --- schedules ----------------------------------------------------------------------------------------------------

def test_schedules_table_marks_silent_models_late_and_lists_late_first():
    rows = logic.schedules_table(
        [decl(), decl("model.p.quiet")], [silence()],
        [run("model.p.orders", -240), run("model.p.quiet", 0), run("model.p.quiet", 60)])
    by = {r["unique_id"]: r for r in rows}
    assert by["model.p.orders"]["status"] == "late" and by["model.p.orders"]["silent_now"] is True
    assert by["model.p.quiet"]["status"] == "ok"
    assert by["model.p.quiet"]["declared_every"] == "1h" and by["model.p.quiet"]["actual_every"] == "1h"


def test_tiles_count_declared_on_time_missed():
    rows = logic.schedules_table([decl(), decl("model.p.quiet")], [silence()], [run("model.p.orders", -240)])
    tiles = logic.tiles(rows, [])
    assert [t.label for t in tiles] == ["Declared schedules", "On time", "Missed", "Sources over limit"]
    assert [t.value for t in tiles[:3]] == [2, 1, 1]


def test_missed_runs_are_windowed_and_newest_first():
    rows = logic.missed_runs([silence(detected=T0), silence("model.p.b", detected=T0 + timedelta(hours=1)),
                              silence("model.p.old", detected=T0 - timedelta(days=30))], since=T0 - timedelta(days=7))
    assert [r["unique_id"] for r in rows] == ["model.p.b", "model.p.orders"]


# --- sources ------------------------------------------------------------------------------------------------------

def test_sources_take_the_latest_lag_per_relation_with_the_merged_threshold():
    rows = logic.sources_table([
        lag("DB.RAW.ORDERS", 100, "source.p.raw.orders", threshold=3600.0, at=T0),
        lag("DB.RAW.ORDERS", 7200, "source.p.raw.orders", threshold=3600.0, at=T0 + timedelta(hours=1)),
        lag("DB.RAW.USERS", 600, "source.p.raw.users", threshold=None),
        {"relation": "DB.RAW.USERS", "metric": "row_count", "value": 5, "observed_at": T0},
    ])
    by = {r["unique_id"]: r for r in rows}
    assert len(rows) == 2
    o = by["source.p.raw.orders"]
    assert (o["lag_minutes"], o["threshold_minutes"], o["limit"], o["status"]) == (120.0, 60.0, "1h", "late")
    u = by["source.p.raw.users"]
    assert (u["limit"], u["status"], u["threshold_minutes"]) == ("no limit set", "ok", None)
    assert rows[0]["unique_id"] == "source.p.raw.orders"  # over limit first


def test_tiles_sources_over_limit():
    src = logic.sources_table([lag("A", 7200, "s.a", threshold=3600), lag("B", 60, "s.b", threshold=3600),
                               lag("C", 9999, "s.c", threshold=None)])
    assert logic.tiles([], src)[3].value == 1


def test_schedules_without_new_columns():
    src = logic.sources_table([lag("A", 600, "s.a"), lag("B", 60, "s.b")])  # METRICS rows with no threshold key
    assert all(r["limit"] == "no limit set" and r["threshold_minutes"] is None and r["status"] == "ok" for r in src)
    assert logic.tiles([], src)[3].value is None  # reads "—"
    assert all("threshold" not in r or r["threshold"] is None for r in logic.chart_rows(src))


def test_chart_rows_feed_lag_vs_limit():
    src = logic.sources_table([lag("DB.RAW.ORDERS", 7200, "s.o", threshold=3600)])
    (row,) = logic.chart_rows(src)
    assert row["lag_minutes"] == 120.0 and row["threshold"] == 60.0 and row["name"] == "ORDERS"


def test_schedules_all_empty():
    assert logic.schedules_table([], [], []) == [] and logic.sources_table([]) == [] and logic.missed_runs([], T0) == []
    t = logic.tiles([], [])
    assert [x.value for x in t] == [0, 0, 0, None]
    assert "meta.sla" in slas.EMPTY_MESSAGE and "expected_every" in slas.SLA_EXAMPLE and "grace" in slas.SLA_EXAMPLE
    assert "1m, capped at expected_every" in slas.SLA_EXAMPLE and "m, h, d, w" in slas.SLA_EXAMPLE


# --- the page -----------------------------------------------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    pytest.importorskip("streamlit")
    monkeypatch.syspath_prepend(str(APP_DIR))
    import os
    for name in [n for n in os.environ if n.startswith("SNOWFLAKE_")] + ["ICECORE_VIEWS_SCHEMA"]:
        monkeypatch.delenv(name, raising=False)
    from lib import queries

    def patch(declared=(), silences=(), nodes=(), metrics=(), boom=None):
        def reader(rows, name):
            def read(*a, **k):
                if boom == name:
                    raise RuntimeError("no session")
                return list(rows)
            return read
        monkeypatch.setattr(queries, "sla_declared", reader(declared, "sla"))
        monkeypatch.setattr(queries, "silence_events", reader(silences, "silence"))
        monkeypatch.setattr(queries, "node_executions", reader(nodes, "nodes"))
        monkeypatch.setattr(queries, "metrics", reader(metrics, "metrics"))

    def render():
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(APP_DIR / "pages" / "schedules.py"), default_timeout=30).run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    return patch, render


def test_page_renders_tiles_tables_and_chart(app):
    patch, render = app
    patch([decl(), decl("model.p.quiet")], [silence()], [run("model.p.orders", -240), run("model.p.quiet", 0)],
          [lag("DB.RAW.ORDERS", 7200, "source.p.raw.orders", threshold=3600.0), lag("DB.RAW.USERS", 60, "s.u")])
    at = render()
    assert at.title[0].value == "Schedules & freshness"
    assert [m.label for m in at.metric] == ["Declared schedules", "On time", "Missed", "Sources over limit"]
    assert [m.value for m in at.metric] == ["2", "1", "1", "1"]
    assert len(at.dataframe) == 3  # schedules, missed runs, sources


def test_schedules_empty_state_shows_the_sla_example(app):
    patch, render = app
    patch()
    at = render()
    assert any("meta.sla" in i.value for i in at.info)
    assert any("expected_every" in c.value for c in at.code)


def test_schedules_empty_state_without_declared_keeps_the_sources(app):
    patch, render = app
    patch(metrics=[lag("DB.RAW.USERS", 60, "s.u")])
    at = render()
    assert any("meta.sla" in i.value for i in at.info) and any("expected_every" in c.value for c in at.code)
    assert len(at.dataframe) == 1 and at.metric[0].value == "0"


def test_schedules_read_error_shows_caption(app):
    patch, render = app
    patch(boom="nodes")
    at = render()
    assert at.info and any("no session" in c.value for c in at.caption)
    assert not at.metric
