"""UC3 Cortex engine: scoring from an existing per-table SNOWFLAKE.ML.ANOMALY_DETECTION model (spec section 6).

Offline tests check rendering and the mapping of DETECT_ANOMALIES rows to the contract. Sandbox tests create
at most one tiny model per run (named with the run_id, in SPIKES), drop it afterwards, and are skipped with the
recorded reason when the capabilities say Cortex is not available to the role.
"""
import uuid

import pytest

from helpers.macros import MacroFailed, call_macro, run_macro, run_macro_with_logs
from test_engine_contract import daily_series_sql

DATABASE, SCHEMA = "ICECORE_SANDBOX", "SPIKES"
SERIES_SQL = "select observed_at, value from t"


def detect_sql(sensitivity="medium", min_consecutive=1, model="DB.S.M", series="k", current=5, observed_at="2026-10-29 06:00:00"):
    return call_macro("cortex__detect_sql", model=model, series=series, series_sql=SERIES_SQL, current_value=current,
                      sensitivity=sensitivity, min_consecutive=min_consecutive, observed_at=observed_at)


# --- rendering (offline) ----------------------------------------------------------------------------


@pytest.mark.parametrize("sensitivity,interval", [("low", 0.99), ("medium", 0.95), ("high", 0.9)])
def test_prediction_interval_follows_sensitivity(sensitivity, interval):
    assert call_macro("_cortex_prediction_interval", sensitivity=sensitivity) == interval
    assert f"'prediction_interval': {interval}" in detect_sql(sensitivity)


def test_detect_sql_calls_the_existing_model_inside_a_catching_block():
    sql = detect_sql(model="OBS.ICECORE_OBS.CX_ORDERS").lower()
    assert "call obs.icecore_obs.cx_orders!detect_anomalies(" in sql
    assert "exception" in sql and "when other" in sql
    assert "create " not in sql  # models are refreshed by a task, never trained here


def test_detect_sql_scores_the_previous_points_for_min_consecutive():
    assert "<= 0" in detect_sql(min_consecutive=1)
    assert "<= 2" in detect_sql(min_consecutive=3)


def test_model_name_is_validated():
    with pytest.raises(MacroFailed, match="identifier"):
        call_macro("cortex__detect_sql", model="m; drop table x", series="k", series_sql=SERIES_SQL, current_value=1,
                   sensitivity="medium", min_consecutive=1)
    with pytest.raises(MacroFailed, match="identifier"):
        call_macro("_cortex_check_model_name", model="a.b.c.d")
    call_macro("_cortex_check_model_name", model='DB."My Schema".M')


def row(y, forecast, lower, upper, is_anomaly):
    return {"Y": y, "FORECAST": forecast, "LOWER_BOUND": lower, "UPPER_BOUND": upper, "IS_ANOMALY": is_anomaly}


def test_result_maps_the_six_fields_above_the_band():
    out = call_macro("_cortex_result", rows=[row(120, 100, 90, 110, True)], min_consecutive=1)
    assert out == {"engine": "cortex", "expected": 100.0, "lower": 90.0, "upper": 110.0, "score": 10.0, "is_anomaly": True}


def test_result_scores_zero_inside_the_band_and_negative_below():
    inside = call_macro("_cortex_result", rows=[row(100, 100, 90, 110, False)], min_consecutive=1)
    assert inside["score"] == 0 and inside["is_anomaly"] is False
    below = call_macro("_cortex_result", rows=[row(80, 100, 90, 110, True)], min_consecutive=1)
    assert below["score"] == -10.0 and below["is_anomaly"] is True


def test_result_requires_previous_points_outside_on_the_same_side():
    current, outside, inside, other_side = row(120, 100, 90, 110, True), row(130, 100, 90, 110, True), row(100, 100, 90, 110, False), row(50, 100, 90, 110, True)
    assert call_macro("_cortex_result", rows=[current, outside, outside], min_consecutive=3)["is_anomaly"] is True
    assert call_macro("_cortex_result", rows=[current, outside, inside], min_consecutive=3)["is_anomaly"] is False
    assert call_macro("_cortex_result", rows=[current, outside, other_side], min_consecutive=3)["is_anomaly"] is False


def test_a_null_bound_is_an_error_not_a_crash():
    assert "error" in call_macro("_cortex_result", rows=[row(120, None, None, None, None)], min_consecutive=1)
    # a null bound on an older point counts as inside the band
    out = call_macro("_cortex_result", rows=[row(120, 100, 90, 110, True), row(130, None, None, None, None)], min_consecutive=2)
    assert out["is_anomaly"] is False


def test_result_without_rows_is_an_error_not_a_crash():
    assert "error" in call_macro("_cortex_result", rows=[], min_consecutive=1)


# --- sandbox ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cortex_model(sf):
    """One tiny two-series model for the whole module, dropped afterwards; skipped when Cortex is unavailable."""
    caps = call_macro("detect_capabilities", record=False)
    if not caps["cortex"]:
        pytest.skip(f"Cortex anomaly detection is not available to this role: {caps['cortex_reason']}")
    run_id = uuid.uuid4().hex.upper()
    table, model = f"ICECORE_CX_T_{run_id}", f"ICECORE_CX_M_{run_id}"
    cur = sf.cursor()
    cur.execute(f"""create table {SCHEMA}.{table} as
        select 'a' as series, dateadd(day, seq4(), '2026-09-01'::timestamp_ntz) as ts, 100 + uniform(0, 5, random(1))::float as y
        from table(generator(rowcount => 40))
        union all select 'b', dateadd(day, seq4(), '2026-09-01'::timestamp_ntz), 10 + uniform(0, 2, random(2))::float
        from table(generator(rowcount => 40))""")
    try:
        try:
            cur.execute(f"""create snowflake.ml.anomaly_detection {SCHEMA}.{model}(
                input_data => system$reference('TABLE', '{SCHEMA}.{table}'),
                series_colname => 'SERIES', timestamp_colname => 'TS', target_colname => 'Y', label_colname => '')""")
        except Exception as exc:
            pytest.skip(f"the role cannot create a Cortex anomaly model: {exc}")
        yield f"{DATABASE}.{SCHEMA}.{model}"
    finally:
        cur.execute(f"drop snowflake.ml.anomaly_detection if exists {SCHEMA}.{model}")
        cur.execute(f"drop table if exists {SCHEMA}.{table}")


def score(model, series, current, sensitivity="medium", min_consecutive=1):
    return call_macro("cortex__score", model=model, series=series, series_sql=SERIES_SQL.replace("from t", "from (select '2026-10-01'::timestamp_ntz as observed_at, 100::float as value where false)"),
                      current_value=current, sensitivity=sensitivity, min_consecutive=min_consecutive,
                      observed_at="2026-10-12 00:00:00")


@pytest.mark.sandbox
def test_trained_model_returns_the_six_fields_with_engine_cortex(cortex_model):
    out = score(cortex_model, "a", 500)
    assert set(out) == {"engine", "expected", "lower", "upper", "score", "is_anomaly"}
    assert out["engine"] == "cortex" and out["is_anomaly"] is True
    assert out["lower"] < out["expected"] < out["upper"] < 500
    assert out["score"] == pytest.approx(500 - out["upper"])


@pytest.mark.sandbox
def test_value_inside_the_band_is_not_an_anomaly(cortex_model):
    out = score(cortex_model, "b", 11)
    assert out["engine"] == "cortex" and out["is_anomaly"] is False and out["score"] == 0


@pytest.mark.sandbox
def test_series_are_scored_by_their_own_band(cortex_model):
    assert score(cortex_model, "a", 11)["is_anomaly"] is True  # 11 is normal for series b, far below a


@pytest.mark.sandbox
def test_high_sensitivity_uses_the_narrower_interval_0_90(cortex_model):
    low, high = score(cortex_model, "a", 103, "low"), score(cortex_model, "a", 103, "high")
    assert high["upper"] - high["lower"] < low["upper"] - low["lower"]


@pytest.mark.sandbox
def test_a_missing_model_is_an_error_value_not_a_failure(cortex_model):
    out = score(f"{DATABASE}.{SCHEMA}.CX_NO_SUCH_MODEL", "a", 1)
    assert set(out) == {"error"} and out["error"]


# --- fallbacks (offline) ----------------------------------------------------------------------------


def test_model_state_from_show_rows():
    day = 86400
    assert call_macro("_cortex_state_of", rows=[], refresh_days=7)["state"] == "missing"
    assert call_macro("_cortex_state_of", rows=[{"age_s": 6 * day}], refresh_days=7)["state"] == "fresh"
    stale = call_macro("_cortex_state_of", rows=[{"age_s": 8 * day}], refresh_days=7)
    assert stale["state"] == "stale" and stale["age_days"] == 8


def test_no_model_given_falls_back_with_model_missing():
    attempt = call_macro("_cortex_attempt", model=None, series=None, series_sql=SERIES_SQL, current_value=1,
                         settings={"sensitivity": "medium", "min_consecutive": 1}, observed_at=None)
    assert attempt["result"] is None and attempt["fallback_reason"] == "model_missing"


# --- query counts (offline, run_query scripted by macros/test_support/it_run_query.sql) --------------------

STUB = ["--vars", '{"it_query_stub": true}']


def counted(macro, kwargs):
    """Run a macro under the scripted run_query: (its result, the queries it issued, in order)."""
    result, logs = run_macro_with_logs("it_call", {"name": macro, "kwargs": kwargs}, STUB)
    return result, [line.split("icecore: query: ")[1] for line in logs if line.startswith("icecore: query: ")]


def test_a_default_auto_score_with_a_working_model_costs_three_queries_and_no_detection():
    result, queries = counted("score", {"series_sql": SERIES_SQL, "current_value": 500, "observed_at": "2026-10-29 06:00:00",
                                        "model": "DB.S.M", "series": "a"})  # engine: the project default, auto
    assert queries == ["history", "model state", "detect"], queries  # no detect_capabilities, no SHOW PARAMETERS
    assert result["engine"] == "cortex" and result["fallback_reason"] is None and result["is_anomaly"] is True


def test_no_model_given_is_model_missing_before_any_query():
    attempt, queries = counted("_cortex_attempt", {"model": None, "series": None, "series_sql": SERIES_SQL, "current_value": 1,
                                                   "settings": {"sensitivity": "medium", "min_consecutive": 1}, "observed_at": None})
    assert queries == [] and attempt["fallback_reason"] == "model_missing"


def test_capabilities_passed_by_the_caller_say_cortex_off_without_any_query():
    attempt, queries = counted("_cortex_attempt", {
        "model": "DB.S.M", "series": "a", "series_sql": SERIES_SQL, "current_value": 1,
        "settings": {"sensitivity": "medium", "min_consecutive": 1}, "observed_at": None,
        "capabilities": {"cortex": False, "cortex_reason": "no class"}})
    assert queries == [] and attempt["fallback_reason"] == "cortex_unavailable"


def test_a_quoted_model_name_keeps_its_dots():
    sql, _ = counted("_cortex_model_state", {"model": 'DB."My.Schema".M', "refresh_days": 7})
    assert sql["state"] == "fresh"  # the scripted SHOW answered; parsing did not raise


@pytest.mark.parametrize("bad", ["7", -1, True])
def test_cortex_refresh_days_must_be_a_number(bad):
    with pytest.raises(MacroFailed, match="cortex_refresh_days"):
        call_macro("_cortex_model_state", model="DB.S.M", refresh_days=bad)


# --- fallbacks through the dispatcher (sandbox) -----------------------------------------------------

HISTORY_22 = daily_series_sql([100 + i % 4 for i in range(22)])
HISTORY_10 = daily_series_sql([100 + i % 4 for i in range(10)])


def dispatch(model, series_sql, extra=None, engine="cortex"):
    kwargs = {"series_sql": series_sql, "current_value": 500, "engine": engine, "sensitivity": "medium",
              "min_consecutive": 1, "observed_at": "2026-10-29 06:00:00", "model": model, "series": "a"}
    return run_macro("it_call", {"name": "score", "kwargs": kwargs}, extra)


@pytest.mark.sandbox
def test_fresh_model_with_enough_history_scores_with_cortex(cortex_model):
    out = dispatch(cortex_model, HISTORY_22)
    assert out["engine"] == "cortex" and out["fallback_reason"] is None and out["is_anomaly"] is True


@pytest.mark.sandbox
def test_no_model_for_the_table_falls_back_with_model_missing(cortex_model):
    out = dispatch(f"{DATABASE}.{SCHEMA}.CX_NO_SUCH_MODEL", HISTORY_22)
    assert out["engine"] == "native" and out["fallback_reason"] == "model_missing"
    assert out["is_anomaly"] is True  # native still scores the series


@pytest.mark.sandbox
def test_model_older_than_the_refresh_period_falls_back_with_model_stale(cortex_model):
    out = dispatch(cortex_model, HISTORY_22, ["--vars", '{"icecore": {"cortex_refresh_days": 0}}'])
    assert out["engine"] == "native" and out["fallback_reason"] == "model_stale"
    assert "days old" in out["fallback_detail"]


@pytest.mark.sandbox
def test_10_days_of_history_falls_back_with_history_too_short(cortex_model):
    out = dispatch(cortex_model, HISTORY_10)
    assert out["engine"] == "native" and out["fallback_reason"] == "history_too_short"


@pytest.mark.sandbox
def test_an_unreadable_schema_is_a_fallback_not_a_failure(cortex_model):
    out = dispatch(f"{DATABASE}.NO_SUCH_SCHEMA_{cortex_model[-8:]}.M", HISTORY_22)
    assert out["engine"] == "native" and out["fallback_reason"] == "cortex_error" and out["fallback_detail"]
