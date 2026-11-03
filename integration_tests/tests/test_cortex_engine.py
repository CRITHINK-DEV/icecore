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
GOOD = "ICECORE_M_0123456789ABCDEF"  # the only shape of model name ICECORE_SCORE accepts
NO_SUCH_MODEL = "ICECORE_M_FFFFFFFFFFFFFFFF"


def call_sql(sensitivity="medium", min_consecutive=1, model=GOOD, series="k", current=5, observed_at="2026-10-29 06:00:00",
             previous=None, schema="obs_x"):
    return call_macro("_cortex_call_sql", model=model, series=series, current_value=current, sensitivity=sensitivity,
                      observed_at=observed_at, schema=schema, refresh_days=7, previous=previous or [])


# --- rendering (offline) ----------------------------------------------------------------------------


@pytest.mark.parametrize("sensitivity,interval", [("low", 0.99), ("medium", 0.95), ("high", 0.9)])
def test_prediction_interval_follows_sensitivity(sensitivity, interval):
    assert call_macro("_cortex_prediction_interval", sensitivity=sensitivity) == interval
    assert f"{interval}, 7" in call_sql(sensitivity)  # the interval, then refresh_days, are the last two arguments


def test_the_call_goes_through_the_score_procedure_inside_a_catching_block():
    sql = call_sql(model=GOOD).lower()
    assert "call " in sql and ".\"icecore_score\"(:payload:model::string, :payload:series::string," in sql  # no database or schema argument
    assert "exception" in sql and "when other" in sql
    assert "detect_anomalies" not in sql and "create " not in sql  # the model is never called directly, never trained here


def test_points_carry_the_current_value_then_the_previous_ones():
    sql = call_sql(previous=[{"ts": "2026-10-28 06:00:00.000", "y": 99.0}], current=5)
    assert '[{"ts": "2026-10-29 06:00:00", "y": 5.0}, {"ts": "2026-10-28 06:00:00.000", "y": 99.0}]' in sql.replace("\\", "")


def test_the_procedure_has_its_own_database_and_schema_as_constants_and_reads_no_caller_sql():
    ddl = "\n".join(call_macro("_icecore_emit_procedures_ddl", schema="obs_x"))
    score = ddl[ddl.index("ICECORE_SCORE"):]
    # SHOW ... LIKE ignores case and treats _ as a wildcard: the result is filtered on the exact name
    assert "LIKE ' || q || model || q" in score and 'WHERE "name" = :model' in score
    assert "REGEXP_LIKE(model, 'ICECORE_M_[0-9A-F]{16}')" in score and "MD5(series)" in score
    assert "(model STRING, series STRING, points ARRAY, prediction_interval FLOAT, refresh_days FLOAT)" in score  # no database or schema argument
    assert re.search(r"db STRING DEFAULT '[A-Z0-9_]+';\s+sch STRING DEFAULT 'OBS_X';", score), score[:600]  # constants fixed at creation
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


@pytest.mark.parametrize("model", ["m; drop table x", "DB.S.ICECORE_M_0123456789ABCDEF", 'ICECORE_M_0123456789abcdef', "ICECORE_M_0123456789ABCDEF\n", "ICECORE_M_0123", "m'--", None])
def test_a_bad_model_name_is_refused_before_any_query(model):
    out, queries = counted("cortex__score", {"model": model, "series": "k", "series_sql": SERIES_SQL, "current_value": 1,
                                             "sensitivity": "medium", "min_consecutive": 1})
    assert queries == [] and out["state"] == "error" and "ICECORE_M_" in out["error"]


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
    table, model = f"ICECORE_CX_T_{run_id}", f"ICECORE_M_{run_id[:16]}"
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
        name = Model(model)
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


PAYLOADS = ["x\\' ); create table ICECORE_SANDBOX.SPIKES.{inj} (a int); select '{sent}' ; $$ -- \n y", "'", "\\", ";", "$$", "\n"]


@pytest.mark.sandbox
def test_the_procedure_has_no_database_or_schema_argument(cortex_model, sf):
    """The signature is the guard: a caller cannot name another database or schema, the old seven-argument call does not compile."""
    proc = f'"{DATABASE}"."{cortex_model.schema}"."ICECORE_SCORE"'
    with pytest.raises(Exception, match="(?i)argument|unknown function|not match"):
        sf.cursor().execute(f"call {proc}('{DATABASE}', 'SPIKES', '{cortex_model}', 'k', to_array(parse_json('[]')), 0.95, 7)")


@pytest.mark.sandbox
def test_the_procedure_binds_its_arguments_and_runs_no_injected_statement(cortex_model, sf):
    """The payload set (quote, backslash, ;, $$, newline, a CREATE TABLE of its own) in every argument: refused or ignored, no
    extra statement ran and no object appeared outside the obs schema."""
    inj, sent = f"INJECTED_{uuid.uuid4().hex.upper()}", "SENTINEL_INJECTED"
    payloads = [p.format(inj=inj, sent=sent) for p in PAYLOADS]
    proc = f'"{DATABASE}"."{cortex_model.schema}"."ICECORE_SCORE"'
    ok_point = [{"ts": "2026-10-12 00:00:00", "y": 500}]
    cur = sf.cursor()

    def call(model=str(cortex_model), series="k", points=ok_point, interval="0.95", days="7"):
        cur.execute(f"call {proc}(%s, %s, to_array(parse_json(%s)), {interval}, {days})", (model, series, json.dumps(points)))
        return json.loads(cur.fetchone()[0])

    for payload in payloads:
        assert "ICECORE_M_" in call(model=payload)["error"]                      # model: refused before any use
        assert call(series=payload).get("state") == "ok"                         # series key: hashed, compared, never SQL
        bad_ts = call(points=[{"ts": payload, "y": 500}])                        # timestamp: not a timestamp, so now (typed), no statement
        assert bad_ts.get("state") == "ok", bad_ts
        assert "numeric y" in call(points=[{"ts": "2026-10-12 00:00:00", "y": payload}])["error"]  # value: refused
    assert "ICECORE_M_" in call(model=str(cortex_model) + "'; select '" + sent + "' --")["error"]
    assert [r for r in cur.execute(f"show tables like '{inj}' in schema {DATABASE}.SPIKES").fetchall()] == []
    assert [r for r in cur.execute(f"show tables like '{inj}' in schema {DATABASE}.{cortex_model.schema}").fetchall()] == []
    texts = [r[0] for r in cur.execute(
        "select query_text from table(information_schema.query_history_by_session(end_time_range_start => dateadd(minute, -10, current_timestamp()), result_limit => 500))").fetchall()]
    assert [t for t in texts if sent in t and not t.lstrip().lower().startswith("call ")] == []


def _named(model, name):
    out = Model(NO_SUCH_MODEL if name == "CX_NO_SUCH_MODEL" else name)
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
                                        "model": GOOD, "series": "a"})  # engine: the project default, auto
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
        "model": GOOD, "series": "a", "series_sql": SERIES_SQL, "current_value": 1,
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
def test_a_bad_model_name_is_a_fallback_not_a_failure(cortex_model):
    out = dispatch("DB.S.ICECORE_M_0123456789ABCDEF", HISTORY_22)
    assert out["engine"] == "native" and out["fallback_reason"] == "cortex_error" and "ICECORE_M_" in out["fallback_detail"]


@pytest.mark.parametrize("name", ["a-b", "a'b", "a b", "a$$b", "a;b", "a.b", ""])
def test_a_database_or_schema_that_is_not_plain_stops_the_procedure_ddl(name):
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        run_macro_with_logs("it_call", {"name": "_icecore_score_constant", "kwargs": {"name": name}})


def test_every_retired_score_signature_is_dropped_before_the_new_one_is_created():
    drops = call_macro("_icecore_retired_score_drops", schema="obs_x")
    assert drops == ['DROP PROCEDURE IF EXISTS "%s"."OBS_X"."ICECORE_SCORE"(STRING, STRING, STRING, STRING, ARRAY, FLOAT, FLOAT)' % DATABASE]


@pytest.mark.parametrize("schema", ["obs-x", "obs'x", "obs x", "obs$$x", "obs;x"])
def test_a_bad_schema_name_stops_setup_before_any_drop(schema):
    """The retired-signature DROPs used to run before the names were validated; the stub raises if a DROP is issued."""
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        run_macro_with_logs("it_call", {"name": "create_emit_procedures", "kwargs": {"schema": schema}},
                            ["--vars", '{"it_query_stub": true, "it_query_stub_forbid_drop": true}'])


def test_the_retired_drops_run_before_the_creates_and_a_failed_drop_is_logged():
    _, logs = run_macro_with_logs("it_call", {"name": "create_emit_procedures", "kwargs": {"schema": "obs_x"}},
                                  ["--vars", '{"it_query_stub": true, "it_drop_status": "icecore-error 3001: insufficient privileges"}'])
    queries = [line.split("icecore: query: ")[1] for line in logs if line.startswith("icecore: query: ")]
    assert queries == ["drop", "create procedure", "create procedure", "create procedure", "create procedure", "create procedure"], queries
    assert any("could not drop a retired ICECORE_SCORE signature" in line and "3001" in line for line in logs), logs


def test_a_clean_drop_logs_no_warning():
    _, logs = run_macro_with_logs("it_call", {"name": "create_emit_procedures", "kwargs": {"schema": "obs_x"}}, STUB)
    assert not any("could not drop" in line for line in logs), logs


def test_the_payload_travels_inside_one_parse_json_not_as_separate_literals():
    sql = call_sql(model=GOOD, series="it's a\\ key")
    assert "payload variant default parse_json(" in sql
    assert ":payload:model::string" in sql and ":payload:series::string" in sql
    assert "ICECORE_M_0123456789ABCDEF'," not in sql  # not a bare literal argument of the CALL


@pytest.mark.sandbox
def test_creating_the_procedures_leaves_exactly_one_score_overload(sf):
    """An earlier ICECORE_SCORE (database and schema arguments) is gone after setup's create_emit_procedures: one overload remains."""
    run_id = uuid.uuid4().hex.upper()
    schema = f"ICECORE_OBS_{run_id}"
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        cur.execute(f"""create procedure "{DATABASE}"."{schema}"."ICECORE_SCORE"(db STRING, sch STRING, model STRING, series STRING, points ARRAY, prediction_interval FLOAT, refresh_days FLOAT)
            returns variant language sql as $$ begin return object_construct('old', true); end; $$""")
        assert len(cur.execute(f"show procedures like 'ICECORE_SCORE' in schema \"{DATABASE}\".\"{schema}\"").fetchall()) == 1
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        rows = cur.execute(f"show procedures like 'ICECORE_SCORE' in schema \"{DATABASE}\".\"{schema}\"").fetchall()
        assert len(rows) == 1 and "ICECORE_SCORE(VARCHAR, VARCHAR, ARRAY, FLOAT, FLOAT)" in rows[0][8], rows
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
