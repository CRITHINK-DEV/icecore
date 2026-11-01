"""UC3 Cortex engine: scoring from an existing per-table SNOWFLAKE.ML.ANOMALY_DETECTION model (spec section 6).

Offline tests check rendering and the mapping of DETECT_ANOMALIES rows to the contract. Sandbox tests create a
schema ICECORE_OBS_<run_id> (the procedures, including ICECORE_SCORE, and one tiny model, trained on md5 series keys as
the refresh task does) per module, drop it afterwards, and are skipped with the recorded reason when the capabilities
say Cortex is not available to the role. One role only: the cross-role behaviour is not measured here (see the PR).
"""
import json
import re
import uuid

import pytest

from helpers.macros import MacroFailed, call_macro, run_macro, run_macro_with_logs
from test_engine_contract import daily_series_sql

DATABASE, SCHEMA = "ICECORE_SANDBOX", "SPIKES"
SERIES_SQL = "select observed_at, value from t"


def call_sql(sensitivity="medium", min_consecutive=1, model="DB.S.M", series="k", current=5, observed_at="2026-10-29 06:00:00",
             previous=None, schema="obs_x"):
    return call_macro("_cortex_call_sql", model=model, series=series, current_value=current, sensitivity=sensitivity,
                      observed_at=observed_at, schema=schema, refresh_days=7, previous=previous or [])


# --- rendering (offline) ----------------------------------------------------------------------------


@pytest.mark.parametrize("sensitivity,interval", [("low", 0.99), ("medium", 0.95), ("high", 0.9)])
def test_prediction_interval_follows_sensitivity(sensitivity, interval):
    assert call_macro("_cortex_prediction_interval", sensitivity=sensitivity) == interval
    assert f"{interval}, 7" in call_sql(sensitivity)  # the interval, then refresh_days, are the last two arguments


def test_the_call_goes_through_the_score_procedure_inside_a_catching_block():
    sql = call_sql(model="OBS.ICECORE_OBS.CX_ORDERS").lower()
    assert "call " in sql and ".\"icecore_score\"('obs', 'icecore_obs', 'cx_orders'" in sql
    assert "exception" in sql and "when other" in sql
    assert "detect_anomalies" not in sql and "create " not in sql  # the model is never called directly, never trained here


def test_points_carry_the_current_value_then_the_previous_ones():
    sql = call_sql(previous=[{"ts": "2026-10-28 06:00:00.000", "y": 99.0}], current=5)
    assert '[{"ts": "2026-10-29 06:00:00", "y": 5.0}, {"ts": "2026-10-28 06:00:00.000", "y": 99.0}]' in sql.replace("\\", "")


def test_the_procedure_matches_the_model_name_exactly_and_reads_no_caller_sql():
    ddl = "\n".join(call_macro("_icecore_emit_procedures_ddl", schema="obs_x"))
    score = ddl[ddl.index("ICECORE_SCORE"):]
    # SHOW ... LIKE ignores case and treats _ as a wildcard: the result is filtered on the exact name
    assert "LIKE ' || q || model || q" in score and 'WHERE "name" = :model' in score
    assert "REGEXP_LIKE(db, '[A-Za-z0-9_$]+')" in score and "MD5(series)" in score
    assert "REPLACE(" not in score.replace("TO_VARCHAR(ts_ntz", "")  # no escaping of caller text: nothing caller-made is concatenated


def literals_removed(sql):
    """The SQL with every single-quoted string literal (backslash and doubled-quote escapes) taken out."""
    return re.sub(r"'(?:\\.|''|[^'\\])*'", "''", sql, flags=re.S)


HOSTILE = "x\\' $$ ; drop table t; -- \n 'y"


def test_a_hostile_series_stays_inside_one_literal():
    outside = literals_removed(call_sql(series=HOSTILE))
    assert "drop" not in outside.lower() and "$$" not in outside
    # the only statement separators are the template's own: declare/begin ... end
    assert outside == literals_removed(call_sql(series="k"))


@pytest.mark.parametrize("model", ["m; drop table x", "a.b.c.d", 'DB."My Schema".M', "a b", "m'--"])
def test_model_name_must_be_a_plain_identifier(model):
    with pytest.raises(MacroFailed, match="plain identifier"):
        call_sql(model=model)


def test_model_parts_default_to_the_obs_schema():
    assert call_macro("_cortex_model_parts", model="m1", schema="obs_x")[1:] == ["OBS_X", "M1"]
    assert call_macro("_cortex_model_parts", model="s1.m1", schema="obs_x")[1:] == ["S1", "M1"]


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


class Model(str):
    """A model's qualified name that also carries the obs schema whose ICECORE_SCORE scores it."""
    schema: str


@pytest.fixture(scope="module")
def cortex_model(sf):
    """ICECORE_OBS_<run_id> with the procedures and one tiny two-series model; dropped afterwards (with the model).
    Skipped when Cortex is unavailable."""
    caps = call_macro("detect_capabilities", record=False)
    if not caps["cortex"]:
        pytest.skip(f"Cortex anomaly detection is not available to this role: {caps['cortex_reason']}")
    run_id = uuid.uuid4().hex.upper()
    schema = f"ICECORE_OBS_{run_id}"
    table, model = f"ICECORE_CX_T_{run_id}", f"ICECORE_CX_M_{run_id}"
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        cur.execute(f"""create table {DATABASE}.{schema}.{table} as
            select md5('a') as series, dateadd(day, seq4(), '2026-09-01'::timestamp_ntz) as ts, 100 + uniform(0, 5, random(1))::float as y
            from table(generator(rowcount => 40))
            union all select md5('b'), dateadd(day, seq4(), '2026-09-01'::timestamp_ntz), 10 + uniform(0, 2, random(2))::float
            from table(generator(rowcount => 40))""")
        try:
            cur.execute(f"""create snowflake.ml.anomaly_detection {DATABASE}.{schema}.{model}(
                input_data => system$reference('TABLE', '{DATABASE}.{schema}.{table}'),
                series_colname => 'SERIES', timestamp_colname => 'TS', target_colname => 'Y', label_colname => '')""")
        except Exception as exc:
            pytest.skip(f"the role cannot create a Cortex anomaly model: {exc}")
        name = Model(f"{DATABASE}.{schema}.{model}")
        name.schema = schema
        yield name
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def score(model, series, current, sensitivity="medium", min_consecutive=1, extra=None):
    kwargs = dict(model=model, series=series, series_sql="select '2026-10-01'::timestamp_ntz as observed_at, 100::float as value where false",
                  current_value=current, sensitivity=sensitivity, min_consecutive=min_consecutive,
                  observed_at="2026-10-12 00:00:00", schema=model.schema)
    return run_macro("it_call", {"name": "cortex__score", "kwargs": kwargs}, extra)


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
    out = score(_named(cortex_model, "CX_NO_SUCH_MODEL"), "a", 1)
    assert out["state"] == "missing" and "error" in out


@pytest.mark.sandbox
def test_the_procedure_binds_its_arguments_and_runs_no_injected_statement(cortex_model, sf):
    """A series, and a model name, full of quotes, backslashes, $$, a semicolon and a newline: no extra statement ran."""
    sentinel = "SENTINEL_INJECTED"
    series = "x\\' ); select '" + sentinel + "' ; $$ -- \n y"
    cur = sf.cursor()
    proc = f'"{DATABASE}"."{cortex_model.schema}"."ICECORE_SCORE"'
    cur.execute(f"call {proc}(%s, %s, %s, %s, to_array(parse_json(%s)), 0.95, 7)",
                (DATABASE, cortex_model.schema, str(cortex_model).split(".")[-1], series, json.dumps([{"ts": "2026-10-12 00:00:00", "y": 500}])))
    out = json.loads(cur.fetchone()[0])
    assert out.get("state") == "ok" and out["rows"] == [], out  # the hashed key is not a series of the model: no row, no error
    bad = sf.cursor().execute(f"call {proc}(%s, %s, %s, 'k', to_array(parse_json('[{{\"y\": 1}}]')), 0.95, 7)",
                              (DATABASE, cortex_model.schema, "m'; select '" + sentinel + "' --")).fetchone()[0]
    assert "plain identifiers" in json.loads(bad)["error"]
    texts = [r[0] for r in sf.cursor().execute(
        "select query_text from table(information_schema.query_history_by_session(end_time_range_start => dateadd(minute, -10, current_timestamp()), result_limit => 300))").fetchall()]
    ran_inside = [t for t in texts if sentinel in t and not t.lstrip().lower().startswith("call ")]
    assert ran_inside == [], ran_inside


def _named(model, name):
    out = Model(f"{DATABASE}.{model.schema}.{name}")
    out.schema = model.schema
    return out


# --- fallbacks (offline) ----------------------------------------------------------------------------


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


def test_a_default_auto_score_with_a_working_model_costs_two_queries_and_no_detection():
    result, queries = counted("score", {"series_sql": SERIES_SQL, "current_value": 500, "observed_at": "2026-10-29 06:00:00",
                                        "model": "DB.S.M", "series": "a"})  # engine: the project default, auto
    # the history count, then ONE call of ICECORE_SCORE (the model's age and the detection happen inside it); no
    # detect_capabilities, no SHOW PARAMETERS, no separate SHOW of the model
    assert queries == ["history", "score call"], queries
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


@pytest.mark.parametrize("bad", ["7", -1, True])
def test_cortex_refresh_days_must_be_a_number(bad):
    with pytest.raises(MacroFailed, match="cortex_refresh_days"):
        call_macro("_cortex_refresh_days", refresh_days=bad)


# --- fallbacks through the dispatcher (sandbox) -----------------------------------------------------

HISTORY_22 = daily_series_sql([100 + i % 4 for i in range(22)])
HISTORY_10 = daily_series_sql([100 + i % 4 for i in range(10)])


def dispatch(model, series_sql, extra=None, engine="cortex"):
    kwargs = {"series_sql": series_sql, "current_value": 500, "engine": engine, "sensitivity": "medium",
              "min_consecutive": 1, "observed_at": "2026-10-29 06:00:00", "model": model, "series": "a",
              "schema": getattr(model, "schema", None)}
    return run_macro("it_call", {"name": "score", "kwargs": kwargs}, extra)


@pytest.mark.sandbox
def test_fresh_model_with_enough_history_scores_with_cortex(cortex_model):
    out = dispatch(cortex_model, HISTORY_22)
    assert out["engine"] == "cortex" and out["fallback_reason"] is None and out["is_anomaly"] is True


@pytest.mark.sandbox
def test_no_model_for_the_table_falls_back_with_model_missing(cortex_model):
    out = dispatch(_named(cortex_model, "CX_NO_SUCH_MODEL"), HISTORY_22)
    assert out["engine"] == "native" and out["fallback_reason"] == "model_missing"
    assert out["is_anomaly"] is True  # native still scores the series


@pytest.mark.sandbox
def test_model_older_than_the_refresh_period_falls_back_with_model_stale(cortex_model):
    out = dispatch(cortex_model, HISTORY_22, ["--vars", '{"icecore": {"cortex_refresh_days": 0}}'])
    assert out["engine"] == "native" and out["fallback_reason"] == "model_stale"
    assert "days" in out["fallback_detail"]


@pytest.mark.sandbox
def test_10_days_of_history_falls_back_with_history_too_short(cortex_model):
    out = dispatch(cortex_model, HISTORY_10)
    assert out["engine"] == "native" and out["fallback_reason"] == "history_too_short"


@pytest.mark.sandbox
def test_an_unreadable_schema_is_a_fallback_not_a_failure(cortex_model):
    bad = Model(f"{DATABASE}.NO_SUCH_SCHEMA_{cortex_model.schema[-8:]}.M")
    bad.schema = cortex_model.schema
    out = dispatch(bad, HISTORY_22)
    assert out["engine"] == "native" and out["fallback_reason"] in ("model_missing", "cortex_error")
