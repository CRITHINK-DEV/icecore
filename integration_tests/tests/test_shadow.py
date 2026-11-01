"""UC3 shadow scoring: the shadow engine's result travels next to the primary verdict, never instead of it.

Offline: the rules that run no query (disabled, same engine, Cortex off) and the rendering of dbt.anomaly.shadow.*.
A query offline would fail to connect, so those tests also prove that nothing was run. Sandbox: a real Cortex model
(one tiny model per module, run_id-named in SPIKES, dropped afterwards; skipped with the recorded reason when Cortex is
unavailable) and one emitted `icecore.shadow` span event read back from the event table.
"""
import json
import os
import uuid

import pytest

from helpers.events import wait_for_events
from helpers.macros import MacroFailed, call_macro, run_macro, run_macro_with_logs
from test_cortex_engine import cortex_model, daily_series_sql  # noqa: F401  (the fixture, shared)

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
NATIVE = {"engine": "native", "expected": 100.0, "lower": 90.0, "upper": 110.0, "score": 390.0, "is_anomaly": True,
          "fallback_reason": None}
CORTEX_OFF = {"cortex": False, "cortex_reason": "SNOWFLAKE.ML.ANOMALY_DETECTION is not available in this account or region"}
SERIES = "select '2026-10-01'::timestamp_ntz as observed_at, 1::float as value"


def shadow(shadow_engine, primary=NATIVE, capabilities=None, **kwargs):
    return run_macro_with_logs("it_call", {"name": "shadow_score", "kwargs": {
        "series_sql": SERIES, "current_value": 500, "primary": primary, "shadow_engine": shadow_engine,
        "capabilities": capabilities, **kwargs}})


# --- no query runs (offline) ------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ["none", None])
def test_shadow_disabled_scores_nothing(engine):
    out, logs = shadow(engine)  # var default is none
    assert out is None and not logs


def test_shadow_equal_to_the_primary_engine_scores_nothing():
    assert shadow("native", NATIVE)[0] is None


def test_unknown_shadow_engine_is_refused():
    with pytest.raises(MacroFailed, match="shadow_engine"):
        shadow("prophet")


def test_cortex_off_makes_no_cortex_call_and_warns_once():
    out, logs = shadow("cortex", capabilities=CORTEX_OFF, model="ICECORE_M_0123456789ABCDEF", series="a")
    assert out is None  # a query would have failed to connect: none ran
    warnings = [line for line in logs if "shadow_engine is cortex" in line]
    assert len(warnings) == 1 and "not available" in warnings[0], logs


def test_disabled_shadow_emits_no_shadow_field():
    # emit would fail to connect offline: it is never reached
    assert run_macro("it_call", {"name": "emit_shadow", "kwargs": {
        "relation": '"DB"."S"."T"', "metric": "row_count", "value": 1, "shadow": None, "primary": NATIVE}}) is None


def test_shadow_fields_are_the_six_contract_fields_plus_the_reason():
    fields = call_macro("_shadow_fields", scored={**NATIVE, "baseline": "flat", "history_points": 9}, fallback_reason=None)
    assert fields == {k: NATIVE[k] for k in ("engine", "expected", "lower", "upper", "score", "is_anomaly", "fallback_reason")}


def test_a_cortex_shadow_that_cannot_score_keeps_its_reason_and_null_bands():
    fields = call_macro("_shadow_fields", scored=None, fallback_reason="model_stale")
    assert fields["engine"] == "cortex" and fields["fallback_reason"] == "model_stale"
    assert all(fields[k] is None for k in ("expected", "lower", "upper", "score", "is_anomaly"))


def test_min_points_reaches_a_native_shadow():
    # offline the native query cannot run, but the argument is validated before it: a bad value is refused
    with pytest.raises(MacroFailed, match="min_points"):
        shadow("native", primary={"engine": "cortex"}, min_points=0)


# --- rendering (offline) ----------------------------------------------------------------------------

CORTEX_RESULT = {"engine": "cortex", "expected": 100.5, "lower": 95.0, "upper": 106.0, "score": 394.0, "is_anomaly": True,
                 "fallback_reason": None}


def attrs(shadow_result, primary=NATIVE, **kwargs):
    return call_macro("_shadow_attributes", relation='"DB"."S"."T"', metric="row_count", value=500,
                      shadow=shadow_result, primary=primary, **kwargs)


def test_shadow_fields_go_under_the_shadow_prefix_and_leave_the_primary_keys_alone():
    a = attrs(CORTEX_RESULT, column="amount", dimension="country=FR")
    assert a["dbt.anomaly.shadow.engine"] == "cortex" and a["dbt.anomaly.shadow.is_anomaly"] is True
    assert (a["dbt.anomaly.shadow.expected"], a["dbt.anomaly.shadow.lower"], a["dbt.anomaly.shadow.upper"]) == (100.5, 95.0, 106.0)
    assert a["dbt.anomaly.shadow.score"] == 394.0
    assert a["dbt.anomaly.shadow.primary_engine"] == "native" and a["dbt.anomaly.shadow.agrees"] is True
    assert a["dbt.metric.value"] == 500 and a["dbt.metric.column"] == "amount" and a["dbt.metric.dimension"] == "country=FR"
    assert not [k for k in a if k.startswith("dbt.anomaly.") and not k.startswith("dbt.anomaly.shadow.")]


def test_disagreement_is_recorded():
    a = attrs({**CORTEX_RESULT, "is_anomaly": False})
    assert a["dbt.anomaly.shadow.agrees"] is False


def test_a_failed_shadow_records_the_reason_without_bands():
    a = attrs({"engine": "cortex", "expected": None, "lower": None, "upper": None, "score": None, "is_anomaly": None,
               "fallback_reason": "model_missing"})
    assert a["dbt.anomaly.shadow.fallback_reason"] == "model_missing"
    assert "dbt.anomaly.shadow.agrees" not in a and "dbt.anomaly.shadow.lower" not in a


# --- sandbox ----------------------------------------------------------------------------------------

HISTORY = daily_series_sql([100 + i % 4 for i in range(22)])


def real_primary():
    return call_macro("score", series_sql=HISTORY, current_value=500, engine="native", sensitivity="medium",
                      min_consecutive=1, observed_at="2026-10-29 06:00:00")


@pytest.mark.sandbox
def test_native_decides_and_cortex_is_the_shadow(cortex_model):
    primary = real_primary()
    out, _ = run_macro_with_logs("it_call", {"name": "shadow_score", "kwargs": {
        "series_sql": HISTORY, "current_value": 500, "primary": primary, "shadow_engine": "cortex",
        "observed_at": "2026-10-29 06:00:00", "model": cortex_model, "series": "a", "schema": cortex_model.schema}})
    assert primary["engine"] == "native" and primary["is_anomaly"] is True  # the verdict is native's
    assert out["engine"] == "cortex" and out["fallback_reason"] is None
    assert out["lower"] < out["expected"] < out["upper"] and out["score"] > 0 and out["is_anomaly"] is True
    assert set(out) == {"engine", "expected", "lower", "upper", "score", "is_anomaly", "fallback_reason"}


@pytest.mark.sandbox
def test_a_cortex_shadow_without_a_model_reports_model_missing(cortex_model):
    out, _ = run_macro_with_logs("it_call", {"name": "shadow_score", "kwargs": {
        "series_sql": HISTORY, "current_value": 500, "primary": NATIVE, "shadow_engine": "cortex",
        "model": "ICECORE_M_FFFFFFFFFFFFFFFF", "series": "a", "schema": cortex_model.schema}})
    assert out["engine"] == "cortex" and out["fallback_reason"] == "model_missing" and out["lower"] is None


@pytest.mark.sandbox
def test_shadow_event_lands_with_the_shadow_keys(sf, dbt):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        since = cur.execute("select dateadd(minute, -2, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]
        relation = f'"{DATABASE}"."SPIKES"."IT_SHADOW_{run_id}"'.upper()
        status = run_macro("it_call", {"name": "emit_shadow", "kwargs": {
            "relation": relation, "metric": "row_count", "value": 500, "shadow": CORTEX_RESULT, "primary": NATIVE}}, obs_vars)
        assert status["statuses"] == ["ok 1"], status
        rows = wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                " and record_type = 'SPAN_EVENT' and record:name::string = 'icecore.shadow'",
            {"db": DATABASE, "schema": schema}, since=since, settle_s=10)
        assert len(rows) == 1
        a = rows[0]["record_attributes"]
        assert a["dbt.anomaly.shadow.engine"] == "cortex" and a["dbt.anomaly.shadow.agrees"] is True
        assert a["dbt.anomaly.shadow.upper"] == 106.0 and a["dbt.node.relation"] == relation
        assert not [k for k in a if k.startswith("dbt.anomaly.") and not k.startswith("dbt.anomaly.shadow.")]
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
