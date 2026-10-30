"""UC3 engine contract: icecore.score returns the same fields whatever engine scores (spec section 6).

Cortex is not shipped yet: no test here may reach SNOWFLAKE.ML. Selection rules are checked offline
through icecore._select_engine; score itself runs in the sandbox on fixed VALUES series.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from helpers.macros import MacroFailed, call_macro

REPO = Path(__file__).resolve().parents[2]
CONTRACT_FIELDS = {"engine", "expected", "lower", "upper", "score", "is_anomaly", "fallback_reason"}


def daily_series_sql(values: list[float], end: datetime = datetime(2026, 10, 29, 6, 0)) -> str:
    """A fixed (observed_at, value) series, one point per day, ending at `end`."""
    start = end - timedelta(days=len(values) - 1)
    rows = ", ".join(
        f"('{(start + timedelta(days=i)).isoformat(sep=' ')}', {v})" for i, v in enumerate(values)
    )
    return f"select column1::timestamp_ntz as observed_at, column2::float as value from values {rows}"


# --- selection rules (offline) ---------------------------------------------------------------


def test_native_never_considers_cortex():
    assert call_macro("_select_engine", engine="native", cortex_unavailable_reason=None, history_days=None) == {
        "engine": "native",
        "fallback_reason": None,
    }


def test_auto_without_cortex_falls_back_to_native():
    choice = call_macro("_select_engine", engine="auto", cortex_unavailable_reason="cortex_unavailable", history_days=30)
    assert choice == {"engine": "native", "fallback_reason": "cortex_unavailable"}


def test_auto_with_cortex_and_10_days_uses_native():
    choice = call_macro("_select_engine", engine="auto", cortex_unavailable_reason=None, history_days=10)
    assert choice == {"engine": "native", "fallback_reason": "history_too_short"}


def test_auto_with_cortex_and_14_days_selects_cortex():
    choice = call_macro("_select_engine", engine="auto", cortex_unavailable_reason=None, history_days=14)
    assert choice == {"engine": "cortex", "fallback_reason": None}


def test_cortex_requested_but_unavailable_falls_back():
    choice = call_macro("_select_engine", engine="cortex", cortex_unavailable_reason="cortex_unavailable", history_days=60)
    assert choice == {"engine": "native", "fallback_reason": "cortex_unavailable"}


def test_cortex_requested_with_short_history_falls_back():
    choice = call_macro("_select_engine", engine="cortex", cortex_unavailable_reason=None, history_days=5)
    assert choice == {"engine": "native", "fallback_reason": "history_too_short"}


def test_cortex_is_reported_unavailable_in_this_version():
    assert call_macro("_cortex_unavailable_reason") == "cortex_unavailable"


def test_unknown_engine_is_rejected():
    with pytest.raises(MacroFailed, match="anomaly engine"):
        call_macro("_anomaly_settings", engine="prophet", sensitivity=None, min_consecutive=None)


def test_unknown_sensitivity_is_rejected():
    with pytest.raises(MacroFailed, match="sensitivity"):
        call_macro("_anomaly_settings", engine="native", sensitivity="extreme", min_consecutive=None)


def test_settings_default_to_project_var():
    assert call_macro("_anomaly_settings", engine=None, sensitivity=None, min_consecutive=None) == {
        "engine": "auto",
        "sensitivity": "medium",
        "min_consecutive": 1,
    }


def test_engine_macros_never_call_cortex_or_write():
    sql = "\n".join(p.read_text() for p in (REPO / "macros" / "engines").glob("*.sql")).lower()
    for forbidden in ("snowflake.ml", "anomaly_detection", "insert ", "merge ", "create table", "create stage"):
        assert forbidden not in sql, forbidden


# --- score in the sandbox ------------------------------------------------------------------------


@pytest.mark.sandbox
def test_native_returns_the_contract_fields():
    result = call_macro(
        "score", series_sql=daily_series_sql([100, 102, 98, 101, 99, 100, 103, 97, 100, 101]),
        current_value=100, engine="native", sensitivity="medium", min_consecutive=1,
    )
    assert CONTRACT_FIELDS <= set(result)
    assert result["engine"] == "native"
    assert result["fallback_reason"] is None
    for key in ("expected", "lower", "upper", "score"):
        assert isinstance(result[key], (int, float)), key
    assert result["lower"] <= result["expected"] <= result["upper"]
    assert result["score"] == 0 and result["is_anomaly"] is False


@pytest.mark.sandbox
def test_auto_without_cortex_scores_native_and_records_fallback():
    result = call_macro(
        "score", series_sql=daily_series_sql([100] * 20 + [101, 99]),
        current_value=500, engine="auto", sensitivity="medium", min_consecutive=1,
    )
    assert CONTRACT_FIELDS <= set(result)
    assert result["engine"] == "native"
    assert result["fallback_reason"] == "cortex_unavailable"
    assert result["is_anomaly"] is True and result["score"] > 0


@pytest.mark.sandbox
def test_auto_with_10_days_of_history_scores_native():
    result = call_macro(
        "score", series_sql=daily_series_sql([10, 11, 9, 10, 12, 10, 9, 11, 10, 10]),
        current_value=10, engine="auto", sensitivity=None, min_consecutive=None,
    )
    assert result["engine"] == "native"
    assert result["fallback_reason"] is not None
