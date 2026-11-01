"""UC3 engine corpus: eight fixed series with the verdict each engine is expected to give (tests/engine_corpus.py).

Native verdicts are asserted exactly (verdict, baseline, sign of the score): any engine change that flips one fails
here, so a change is made on purpose, with the table below updated in the same PR. Cortex cases run in the sandbox
only, on one tiny model trained on the series (named ICECORE_M_<16 hex>, in the schema ICECORE_OBS_<run_id> that is dropped
afterwards); the verdicts are recorded and the differences from native are listed in the test output. A drift from the
recorded Cortex verdicts is a warning; a series that had a recorded verdict and now returns an error fails.
"""
import hashlib
import json
import uuid
import warnings

import pytest

from engine_corpus import CORPUS, values_sql
from helpers.macros import call_macro, run_macro

DATABASE, SCHEMA = "ICECORE_SANDBOX", "SPIKES"

# series: (native is_anomaly, native baseline, sign of the native score)
# step_change, trend and holiday_dip show native's known limits: a median band flags a lasting level shift, a growing
# series and a holiday dip (spec section 6). The corpus records that so it is a decision to change them.
NATIVE_EXPECTED = {
    "flat": (False, "seasonal", 0),
    "seasonal": (True, "seasonal", 1),
    "single_outlier": (True, "seasonal", 1),
    "step_change": (True, "seasonal", 1),
    "trend": (True, "seasonal", 1),
    "sparse": (False, "insufficient_history", 0),
    "holiday_dip": (True, "seasonal", -1),
    "noisy": (False, "seasonal", 0),
}

# series: Cortex is_anomaly, or None when the model has no band for it (sparse: the fixture trains only series with at least
# 12 points, Cortex's own minimum per series, so this is the test's filter). Differences drift as a warning, never a failure.
# Recorded 1 Nov 2026 (sandbox, medium). Differences from native: flat and noisy (Cortex flags 101 on a series whose
# values cycle tightly: its band is narrower than native's), step_change (Cortex follows the new level, native does
# not) and sparse (5 points: no model for the series).
CORTEX_EXPECTED = {
    "flat": True, "seasonal": True, "single_outlier": True, "step_change": False, "trend": True,
    "sparse": None, "holiday_dip": True, "noisy": True,
}


def test_the_corpus_has_the_eight_series():
    assert set(CORPUS) == set(NATIVE_EXPECTED) == {
        "flat", "seasonal", "single_outlier", "step_change", "trend", "sparse", "holiday_dip", "noisy"}


def test_corpus_is_deterministic():
    import importlib
    import engine_corpus
    assert values_sql(CORPUS["seasonal"]["points"]) == values_sql(importlib.reload(engine_corpus).CORPUS["seasonal"]["points"])


def cortex_findings(observed, errors, expected=None):
    """(broke, drift). broke: series that had a recorded verdict and now return an error (a failure). drift: series whose
    verdict changed (a warning: Cortex is ML output); an error is never drift, and a series whose recorded value is None
    (no band) that errors is as recorded."""
    expected = CORTEX_EXPECTED if expected is None else expected
    broke = {k: errors[k][:120] for k in errors if expected[k] is not None}
    drift = {k: (expected[k], v) for k, v in observed.items() if k not in errors and expected[k] != v}
    return broke, drift


def test_an_error_on_a_series_with_a_recorded_verdict_fails_and_is_not_drift():
    broke, drift = cortex_findings({"flat": None, "sparse": None}, {"flat": "no model"})
    assert list(broke) == ["flat"] and drift == {}


def test_a_changed_verdict_is_drift_not_a_failure():
    broke, drift = cortex_findings({"flat": False, "sparse": None}, {})
    assert broke == {} and drift == {"flat": (True, False)}


def test_an_error_on_a_series_recorded_without_a_band_is_as_recorded():
    broke, drift = cortex_findings({"sparse": None}, {"sparse": "no band"})
    assert broke == {} and drift == {}


def test_a_verdict_where_none_was_recorded_is_drift():
    broke, drift = cortex_findings({"sparse": True}, {})
    assert broke == {} and drift == {"sparse": (None, True)}


def native_verdict(name):
    case = CORPUS[name]
    return call_macro("native__score", series_sql=values_sql(case["points"]), current_value=case["value"],
                      sensitivity="medium", min_consecutive=1, observed_at=case["at"].isoformat(sep=" "))


@pytest.mark.sandbox
@pytest.mark.parametrize("name", sorted(NATIVE_EXPECTED))
def test_native_verdict_matches_the_corpus(name):
    result = native_verdict(name)
    flagged, baseline, sign = NATIVE_EXPECTED[name]
    assert (result["is_anomaly"], result["baseline"], (result["score"] > 0) - (result["score"] < 0)) == (flagged, baseline, sign), result


@pytest.fixture(scope="module")
def corpus_model(sf):
    """ICECORE_OBS_<run_id> with the procedures and one model on every series with enough points (trained on md5 series
    keys, as the refresh task does); dropped with the schema. Skipped with the reason when Cortex is unavailable.
    "sparse" has no band because of this fixture's own `>= 12` points filter (Cortex needs 12 per series), not a Cortex rule
    on a table."""
    caps = call_macro("detect_capabilities", record=False)
    if not caps["cortex"]:
        pytest.skip(f"Cortex anomaly detection is not available to this role: {caps['cortex_reason']}")
    run_id = uuid.uuid4().hex.upper()
    schema = f"ICECORE_OBS_{run_id}"
    table, model = f"ICECORE_CORPUS_T_{run_id}", f"ICECORE_M_{run_id[:16]}"
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        cur.execute(f"create table {DATABASE}.{schema}.{table} (series varchar, ts timestamp_ntz, y float)")
        rows = [(hashlib.md5(k.encode()).hexdigest(), t, v) for k, c in CORPUS.items() if len(c["points"]) >= 12 for t, v in c["points"]]
        cur.executemany(f"insert into {DATABASE}.{schema}.{table} values (%s, %s, %s)", rows)
        try:
            cur.execute(f"""create snowflake.ml.anomaly_detection {DATABASE}.{schema}.{model}(
                input_data => system$reference('TABLE', '{DATABASE}.{schema}.{table}'),
                series_colname => 'SERIES', timestamp_colname => 'TS', target_colname => 'Y', label_colname => '')""")
        except Exception as exc:
            pytest.skip(f"the role cannot create a Cortex anomaly model on the corpus: {exc}")
        yield model, schema
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


@pytest.mark.sandbox
def test_cortex_verdicts_are_recorded_and_differences_listed(corpus_model, capsys):
    model, schema = corpus_model
    observed, errors, diffs = {}, {}, []
    for name, case in sorted(CORPUS.items()):
        out = call_macro("cortex__score", model=model, series=name, series_sql=values_sql(case["points"]),
                         current_value=case["value"], sensitivity="medium", min_consecutive=1,
                         observed_at=case["at"].isoformat(sep=" "), schema=schema)
        if "error" in out:
            observed[name], errors[name] = None, out["error"]
        else:
            observed[name] = out.get("is_anomaly")
        if observed[name] != NATIVE_EXPECTED[name][0]:
            diffs.append(f"{name}: native {NATIVE_EXPECTED[name][0]}, cortex {observed[name]}"
                         + (f" ({errors[name][:80]})" if name in errors else ""))
    with capsys.disabled():
        print("\nengine corpus, cortex verdicts:", observed)
        print("engine corpus, differences native vs cortex:", diffs or "none")
    assert set(observed) == set(CORTEX_EXPECTED)
    broke, drift = cortex_findings(observed, errors)
    assert not broke, f"Cortex returned an error for series that had a recorded verdict: {broke}"
    if drift:
        warnings.warn(f"engine corpus: Cortex verdicts drifted from the recorded ones (recorded, now): {drift}; "
                      "review and update CORTEX_EXPECTED if intended", stacklevel=1)
