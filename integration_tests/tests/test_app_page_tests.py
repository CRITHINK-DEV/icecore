"""Tests & anomalies page: logic and an AppTest render with the reads patched. Needs streamlit (1.50 venv):

    cd integration_tests && python -m pytest --noconftest tests/test_app_page_tests.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
ENTRY = str(APP_DIR / "streamlit_app.py")
T0 = datetime(2026, 11, 1, 12, 0)


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    import os
    for name in [n for n in os.environ if n.startswith("SNOWFLAKE_")] + ["ICECORE_VIEWS_SCHEMA"]:
        monkeypatch.delenv(name, raising=False)


def _test(uid, status, name="not_null", engine=None, at=T0, **kw):
    return {"test_unique_id": uid, "test_name": name, "test_type": "generic", "status": status, "engine": engine,
            "executed_at": at, "failures": 0, **kw}


def _metric(day, value, engine=None, node="test.p.vol", lower=None, upper=None, anomaly=False, score=None,
            relation="DB.S.ORDERS", metric="row_count", **kw):
    return {"relation": relation, "metric": metric, "column": None, "dimension": None,
            "observed_at": T0 + timedelta(days=day), "value": value, "anomaly_engine": engine,
            "anomaly_expected": value if engine else None, "anomaly_lower": lower, "anomaly_upper": upper,
            "anomaly_score": score, "anomaly_is_anomaly": anomaly, "node_unique_id": node, **kw}


def test_results_table_failures_first_with_engine():
    from lib.pages import tests as t

    results = [_test("test.p.a", "pass", "a"), _test("test.p.b", "error", "b"), _test("test.p.vol", "warn", "volume"),
               _test("test.p.c", "fail", "c", engine="native")]
    metrics = [_metric(0, 10, "native"), _metric(0, 10, "cortex")]
    rows = t.results_table(results, metrics)
    assert [r["status"] for r in rows] == ["failed", "failed", "warning", "ok"]
    engines = {r["unique_id"]: r["engine"] for r in rows}
    assert engines["test.p.vol"] == "both"
    assert engines["test.p.c"] == "native"
    assert engines["test.p.a"] == "—"


def test_results_table_keeps_the_latest_result_of_each_test():
    from lib.pages import tests as t

    rows = t.results_table([_test("test.p.a", "pass", at=T0), _test("test.p.a", "fail", at=T0 + timedelta(hours=1)),
                            _test("test.p.a", "pass", at=T0 - timedelta(hours=1))], [])
    assert len(rows) == 1 and rows[0]["status"] == "failed"


def test_series_for_one_engine_and_two_engines():
    from lib.pages import tests as t

    one = [_metric(1, 5, "native"), _metric(0, 4, "native")]
    got = t.series_for(one, "DB.S.ORDERS", "row_count")
    assert list(got) == ["native"] and [p["value"] for p in got["native"]] == [4, 5]
    two = one + [_metric(0, 4, "cortex"), _metric(1, 5, "cortex"), _metric(-1, 3, None)]
    got = t.series_for(two, "DB.S.ORDERS", "row_count")
    assert list(got) == ["cortex", "native"]
    assert [p["value"] for p in got["native"]] == [3, 4, 5]  # the unscored early point joins both engines
    assert [p["value"] for p in got["cortex"]] == [3, 4, 5]
    assert t.series_for(two, "DB.S.OTHER", "row_count") == {}
    only_unscored = t.series_for([_metric(0, 1, None)], "DB.S.ORDERS", "row_count")
    assert list(only_unscored) == ["unscored"]


def test_series_for_reads_shadow_columns_as_a_second_engine():
    from lib.pages import tests as t

    row = _metric(0, 10, "native", lower=8, upper=12, score=0.1,
                  shadow_engine="cortex", shadow_expected=9, shadow_lower=7, shadow_upper=11, shadow_score=0.4,
                  shadow_is_anomaly=True)
    got = t.series_for([row], "DB.S.ORDERS", "row_count")
    assert sorted(got) == ["cortex", "native"]
    assert got["cortex"][0]["anomaly_lower"] == 7 and got["cortex"][0]["anomaly_is_anomaly"] is True
    assert got["native"][0]["anomaly_lower"] == 8 and not got["native"][0]["anomaly_is_anomaly"]


def test_tiles():
    from lib.pages import tests as t

    results = [_test("test.p.a", "fail"), _test("test.p.b", "warn"), _test("test.p.c", "pass")]
    metrics = [_metric(0, 1, "native", node="test.p.v1"), _metric(1, 99, "native", anomaly=True, node="test.p.v1"),
               _metric(1, 5, "cortex", relation="DB.S.USERS", node="test.p.v2")]
    tiles = {x.label: x.value for x in t.tiles(results, metrics)}
    assert tiles == {"Failing": 1, "Warning": 1, "Anomalies": 1, "Monitors scored by Cortex": 1}


def test_score_caption():
    from lib.pages import tests as t

    pts = [_metric(0, 1, "native", score=0.2), _metric(1, 9, "native", score=0.95, anomaly=True)]
    assert t.score_caption(pts, "native") == "native: latest score 0.95, 1 anomalous point(s)."
    assert "not scored" in t.score_caption([_metric(0, 1, None)], "cortex")


def test_filter_status_and_unknown_engines():
    from lib.pages import tests as t

    rows = [{"status": "failed"}, {"status": "warning"}, {"status": "ok"}]
    assert [r["status"] for r in t.filter_status(rows, ["failed", "warning"])] == ["failed", "warning"]
    assert t.filter_status(rows, []) == rows
    assert t.engine_label({"x", "y"}) == "x + y"


def test_tests_all_empty():
    from lib.pages import tests as t

    assert t.results_table([], []) == [] and t.series_list([]) == []
    assert [x.value for x in t.tiles([], [])] == [0, 0, 0, 0]
    assert t.series_for([], "r", "m") == {}


def test_tests_without_optional_columns():
    from lib.pages import tests as t

    # an older view: rows without engine, score, root cause or the metric's node id
    rows = t.results_table([{"test_unique_id": "test.p.a", "status": "pass"}], [{"relation": "r", "metric": "m",
                                                                                 "observed_at": T0, "value": 1}])
    assert rows[0]["engine"] == "—" and rows[0]["test"] == "test.p.a"


def test_names_with_markup_stay_text():
    from lib.pages import tests as t

    rows = t.results_table([_test("test.p.x", "fail", name="<b>x</b>")], [])
    assert rows[0]["test"] == "<b>x</b>"  # rendered by st.dataframe as text


# --- the page -----------------------------------------------------------------------------------------------------

def _run(monkeypatch, results=None, metrics=None, error=None):
    from lib import queries

    def reader(rows):
        def read(**_):
            if error:
                raise RuntimeError(error)
            return rows or []
        return read

    monkeypatch.setattr(queries, "test_results", reader(results))
    monkeypatch.setattr(queries, "metrics", reader(metrics))
    at = AppTest.from_file(ENTRY, default_timeout=30)
    at.switch_page("pages/tests_and_anomalies.py")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_tests_read_error_shows_caption(monkeypatch):
    at = _run(monkeypatch, error="boom")
    assert at.info
    assert any("boom" in c.value for c in at.caption)


def test_page_renders_with_data(monkeypatch):
    today = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    metrics = [{**_metric(0, 10 + i, e, lower=5, upper=20, score=0.1), "observed_at": today - timedelta(days=3 - i)}
               for i in range(3) for e in ("native", "cortex")]
    results = [_test("test.p.a", "fail", at=today), _test("test.p.vol", "warn", "volume", at=today)]
    at = _run(monkeypatch, results, metrics)
    assert at.title[0].value == "Tests & anomalies"
    labels = [m.label for m in at.metric]
    assert labels == ["Failing", "Warning", "Anomalies", "Monitors scored by Cortex"]
    assert [m.value for m in at.metric] == ["1", "1", "0", "1"]
    # both engines scored the series: two captions with scores
    assert sum("latest score" in c.value for c in at.caption) == 2


def test_default_series_is_the_anomalous_one_else_the_longest():
    from lib.pages import tests as logic

    def pt(metric, i, anomalous=False):
        return {"relation": "R", "metric": metric, "observed_at": f"2026-11-0{i}T00:00:00", "value": 1,
                "anomaly_is_anomaly": anomalous}

    calm = [pt("a_one", 1), pt("b_many", 1), pt("b_many", 2), pt("b_many", 3)]
    series = logic.series_list(calm)
    assert series[logic.default_series_index(series, calm)]["metric"] == "b_many"
    flagged = calm + [pt("c_bad", 1, True)]
    series = logic.series_list(flagged)
    assert series[logic.default_series_index(series, flagged)]["metric"] == "c_bad"
    assert logic.default_series_index([], []) == 0
