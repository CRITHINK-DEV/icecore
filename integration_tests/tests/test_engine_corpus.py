"""UC3 engine corpus: eight fixed series with the verdict each engine is expected to give (tests/engine_corpus.py).

Native verdicts are asserted exactly (verdict, baseline, sign of the score): any engine change that flips one fails
here, so a change is made on purpose, with the table below updated in the same PR. Cortex cases run in the sandbox
only, on one tiny model trained on the series (run_id-named in SPIKES, dropped afterwards); the verdicts are recorded
and the differences from native are listed in the test output (and asserted, so a drift is noticed).
"""
import uuid

import pytest

from engine_corpus import CORPUS, values_sql
from helpers.macros import call_macro

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

# series: Cortex is_anomaly, or None when the model has no band for it (too few points to train).
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
    """One model on every series that has enough points; skipped with the reason when Cortex is unavailable."""
    caps = call_macro("detect_capabilities", record=False)
    if not caps["cortex"]:
        pytest.skip(f"Cortex anomaly detection is not available to this role: {caps['cortex_reason']}")
    run_id = uuid.uuid4().hex.upper()
    table, model = f"ICECORE_CORPUS_T_{run_id}", f"ICECORE_CORPUS_M_{run_id}"
    cur = sf.cursor()
    cur.execute(f"create table {SCHEMA}.{table} (series varchar, ts timestamp_ntz, y float)")
    try:
        rows = [(k, t, v) for k, c in CORPUS.items() if len(c["points"]) >= 12 for t, v in c["points"]]
        cur.executemany(f"insert into {SCHEMA}.{table} values (%s, %s, %s)", rows)
        try:
            cur.execute(f"""create snowflake.ml.anomaly_detection {SCHEMA}.{model}(
                input_data => system$reference('TABLE', '{SCHEMA}.{table}'),
                series_colname => 'SERIES', timestamp_colname => 'TS', target_colname => 'Y', label_colname => '')""")
        except Exception as exc:
            pytest.skip(f"the role cannot create a Cortex anomaly model on the corpus: {exc}")
        yield f"{DATABASE}.{SCHEMA}.{model}"
    finally:
        cur.execute(f"drop snowflake.ml.anomaly_detection if exists {SCHEMA}.{model}")
        cur.execute(f"drop table if exists {SCHEMA}.{table}")


@pytest.mark.sandbox
def test_cortex_verdicts_are_recorded_and_differences_listed(corpus_model, capsys):
    observed, diffs = {}, []
    for name, case in sorted(CORPUS.items()):
        out = call_macro("cortex__score", model=corpus_model, series=name, series_sql=values_sql(case["points"]),
                         current_value=case["value"], sensitivity="medium", min_consecutive=1,
                         observed_at=case["at"].isoformat(sep=" "))
        observed[name] = out.get("is_anomaly") if "error" not in out else None
        if observed[name] != NATIVE_EXPECTED[name][0]:
            diffs.append(f"{name}: native {NATIVE_EXPECTED[name][0]}, cortex {observed[name]}"
                         + (f" ({out['error'][:80]})" if "error" in out else ""))
    with capsys.disabled():
        print("\nengine corpus, cortex verdicts:", observed)
        print("engine corpus, differences native vs cortex:", diffs or "none")
    assert observed == CORTEX_EXPECTED, observed
