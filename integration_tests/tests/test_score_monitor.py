"""UC3 wiring: icecore.score_monitor_metrics (macros/monitors/score_monitor.sql) and the five generic tests that call it.

Offline: the scoring query is replaced by a stub (macros/test_support/it_score_monitor.sql), so these tests check what
the monitors do with a result: one query per test, the contract passed to emit_metric (`anomaly`), insufficient history
(not scored, never an error), the var that switches scoring off, per-test overrides, and that the five tests take the
new arguments (parse). The sandbox end-to-end tests are in test_anomaly_e2e.py.
"""
import json

import pytest

from helpers.macros import MacroFailed, invoke, run_macro_with_logs

REL = {"database": "ICECORE_SANDBOX", "schema": "SPIKES", "identifier": "ORDERS", "quoted": True}
VOLUME = {"metric": "row_count", "value": 1000}


def score(points, kwargs=None, case="quiet", cfg=None):
    vars_ = {"it_monitor_case": case}
    if cfg:
        vars_["icecore"] = cfg
    result, logs = run_macro_with_logs("it_score_monitor", {"rel": REL, "points": points, "kwargs": kwargs or {}},
                                       ["--vars", json.dumps(vars_)])
    return result, logs


def queries(logs) -> list:
    return [line for line in logs if "stub query" in line]


def test_an_anomaly_reaches_emit_metric_as_the_contract_and_adds_the_warn_event():
    out, logs = score([VOLUME], case="anomaly")
    result = out["results"][0]
    assert result["engine"] == "native" and result["is_anomaly"] is True
    assert (result["expected"], result["lower"], result["upper"]) == (100.0, 90.0, 110.0)
    metric, anomaly = out["events"]
    assert metric["attributes"]["dbt.anomaly.is_anomaly"] is True and metric["attributes"]["dbt.anomaly.engine"] == "native"
    assert anomaly["kind"] == "anomaly" and anomaly["level"] == "warn"
    assert len(queries(logs)) == 1


def test_a_value_inside_the_band_is_scored_and_not_an_anomaly():
    out, _ = score([VOLUME], case="quiet")
    assert out["results"][0]["is_anomaly"] is False
    assert [e["kind"] for e in out["events"]] == ["metric"]


def test_insufficient_history_is_not_scored_and_says_why_without_an_error():
    out, logs = score([VOLUME], case="short")
    assert out["results"] == [None]
    assert [e["kind"] for e in out["events"]] == ["metric"] and "dbt.anomaly.is_anomaly" not in out["events"][0]["attributes"]
    assert any("insufficient history" in line and "row_count" in line for line in logs)


def test_a_failed_read_warns_and_scores_nothing_instead_of_raising():
    out, logs = score([VOLUME], case="error")
    assert out["results"] == [None]
    assert any("warning" in line and "forced scoring failure" in line for line in logs)


def test_all_metrics_of_a_test_share_one_query_and_wide_tests_chunk_at_25():
    points = [{"metric": "null_rate", "value": 0.0, "column": f"C{i}"} for i in range(5)]
    out, logs = score(points, case="first_anomaly")
    assert [r["is_anomaly"] for r in out["results"]] == [True, False, False, False, False]
    assert len(queries(logs)) == 1 and queries(logs)[0].endswith("stub query 5 point(s)")
    _, logs = score([{"metric": "window_rows", "value": 1, "column": "C", "dimension": f"C=v{i}"} for i in range(30)])
    assert [q.split("stub query ")[1] for q in queries(logs)] == ["25 point(s)", "5 point(s)"]


def test_scoring_can_be_switched_off_with_the_monitor_score_var():
    out, logs = score([VOLUME], case="anomaly", cfg={"monitor_score": False})
    assert out["results"] == [None] and not queries(logs)


def test_a_null_value_is_not_scored():
    out, logs = score([{"metric": "avg", "value": None, "column": "A"}], case="anomaly")
    assert out["results"] == [None] and not queries(logs)


def test_the_history_window_and_key_are_the_lag_aware_readers():
    sql, _ = run_macro_with_logs("it_score_monitor_sql", {"rel": REL, "point": {"metric": "avg", "column": "A", "dimension": "d=1"}})
    assert 'from "ICECORE_OBS"."METRICS"' in sql or '."METRICS"' in sql
    assert "dateadd(minute, -1," in sql and "dateadd(day, -30," in sql
    assert "metric = 'avg'" in sql and "\"COLUMN\" is not distinct from 'A'" in sql and "dimension is not distinct from 'd=1'" in sql


def test_per_test_engine_overrides_the_var():
    # var says cortex: the 10-day series (under 14 days) falls back to native and says why; the test's own engine=native
    # scores with native outright, whatever the var says
    out, _ = score([VOLUME], case="young", cfg={"anomaly_engine": "cortex"})
    assert out["results"][0]["engine"] == "native" and out["results"][0]["fallback_reason"] == "history_too_short"
    out, _ = score([VOLUME], kwargs={"engine": "native"}, case="young", cfg={"anomaly_engine": "cortex"})
    assert out["results"][0]["engine"] == "native" and out["results"][0]["fallback_reason"] is None


@pytest.mark.parametrize("kwargs", [{"engine": "magic"}, {"sensitivity": "extreme"}, {"min_consecutive": 0},
                                    {"lookback_days": 0}, {"min_points": 0}, {"shadow_engine": "gpt"}])
def test_invalid_settings_are_refused(kwargs):
    with pytest.raises(MacroFailed):
        score([VOLUME], kwargs=kwargs)


# --- the five tests take the arguments (parse only, no connection) --------------------------------

def test_the_five_tests_take_the_scoring_arguments_and_refuse_a_bad_engine():
    assert invoke(["deps"]).success
    res = invoke(["parse"])
    assert res.success
    nodes = {n.name: n for n in res.result.nodes.values() if n.resource_type.value == "test"}
    for name, test in [("it_anom_volume_override", "volume"), ("it_anom_freshness_override", "freshness"),
                       ("it_anom_column_override", "column_anomaly"), ("it_anom_all_columns_override", "all_columns_anomaly"),
                       ("it_anom_dimension_override", "dimension_anomaly")]:
        assert nodes[name].test_metadata.name == test
        assert nodes[name].test_metadata.kwargs["engine"] == "native" and nodes[name].test_metadata.kwargs["sensitivity"] == "high"
    assert not invoke(["parse", "--vars", json.dumps({"it_anom_engine": "magic"})]).success
