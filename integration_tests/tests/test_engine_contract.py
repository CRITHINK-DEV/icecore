"""UC3 engine contract: icecore.score returns the same fields whatever engine scores (spec section 6).

Cortex scoring is tested in test_cortex_engine.py: no test here may reach SNOWFLAKE.ML. Selection rules are checked offline
through icecore._select_engine; score itself runs in the sandbox on fixed VALUES series.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import os
import re

import yaml

from helpers.env import PLACEHOLDER_ENV, placeholder_env
from helpers.macros import MacroFailed, call_macro, invoke

REPO = Path(__file__).resolve().parents[2]
CONTRACT_FIELDS = {"engine", "expected", "lower", "upper", "score", "is_anomaly", "fallback_reason"}


def daily_series_sql(values: list[float], end: datetime = datetime(2026, 10, 29, 6, 0)) -> str:
    """A fixed (observed_at, value) series, one point per day, ending at `end`."""
    start = end - timedelta(days=len(values) - 1)
    rows = ", ".join(
        f"('{(start + timedelta(days=i)).isoformat(sep=' ')}', {v})" for i, v in enumerate(values)
    )
    return f"select column1::timestamp_ntz as observed_at, column2::float as value from values {rows}"


# --- the macro bridge (offline) ---------------------------------------------------------------


def test_macro_bridge_leaves_no_placeholder_behind():
    call_macro("_cortex_unavailable_reason")
    assert all(os.environ.get(key) != value for key, value in PLACEHOLDER_ENV.items() if value != "/dev/null")


def test_placeholder_env_fills_only_missing_values_and_restores(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ROLE", "REAL_ROLE")
    with placeholder_env():
        assert os.environ["SNOWFLAKE_ACCOUNT"] == "placeholder"
        assert os.environ["SNOWFLAKE_ROLE"] == "REAL_ROLE"
    assert "SNOWFLAKE_ACCOUNT" not in os.environ
    assert os.environ["SNOWFLAKE_ROLE"] == "REAL_ROLE"


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


@pytest.mark.parametrize("value", [1.5, True, "2", 0])
def test_min_consecutive_must_be_a_positive_integer(value):
    with pytest.raises(MacroFailed, match="min_consecutive"):
        call_macro("_anomaly_settings", engine="native", sensitivity=None, min_consecutive=value)


def test_package_declares_the_anomaly_var_defaults():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))["vars"]["icecore"]
    assert cfg["anomaly_engine"] == "auto"
    assert cfg["anomaly_sensitivity"] == "medium"
    assert cfg["anomaly_min_consecutive"] == 1


def test_a_model_calling_score_parses():
    """models/engines/probe_engine_score.sql calls icecore.score; parse renders it with execute false."""
    assert invoke(["parse", "--no-partial-parse"]).success


# Any create (table of any kind, stage, dynamic table, snowflake.ml object...), any insert (into, overwrite, all), truncate,
# alter, copy into, merge into, drop, delete, update. A word after a dot is a Jinja or Python method (`result.update(...)`).
WRITE_STATEMENTS = [
    r"(?<!\.)\bcreate\b", r"\binsert\b", r"\btruncate\b", r"\balter\b", r"copy\s+into", r"merge\s+into",
    r"(?<!\.)\bdrop\b", r"(?<!\.)\bdelete\b", r"(?<!\.)\bupdate\b",
]


def writes_in(sql: str) -> list[str]:
    """The write statements found in macro text, ignoring Jinja comments, SQL comments, case and whitespace."""
    sql = re.sub(r"\{#.*?#\}", " ", sql, flags=re.S)          # Jinja comments
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)           # SQL block comments: create/**/table is create table
    sql = re.sub(r"--[^\n]*", " ", sql).lower()                 # SQL line comments
    return [pattern for pattern in WRITE_STATEMENTS if re.search(pattern, sql)]


@pytest.mark.parametrize("sql", [
    "create or replace snowflake.ml.anomaly_detection m(...)", "create  or  replace table t as select 1", "CREATE STAGE s",
    "create transient table t (a int)", "create temp table t as select 1", "create temporary table t (a int)",
    "create dynamic table t target_lag = '1 minute' as select 1", "create/**/table t (a int)", "create /* x */ or replace table t (a int)",
    "insert  into t values (1)", "insert overwrite into t select 1", "insert all into t select 1", "truncate table t",
    "copy into t from @s", "alter table t add column c int", "merge into t using s", "drop table t", "delete from t",
    "update t set a = 1",
])
def test_the_no_write_check_catches_what_a_substring_match_missed(sql):
    assert writes_in(sql), sql


def test_the_no_write_check_ignores_reads_and_comments():
    assert writes_in("select updated_at from t") == []
    assert writes_in("{% do result.update({1: 2}) %}") == []  # a Jinja dict method, not SQL
    assert writes_in("{#- never trained here: no create table, no drop -#} select 1") == []
    assert writes_in("select 1 -- no create table here\n/* nor alter or truncate */") == []


def test_engine_macros_never_write_and_only_cortex_sql_calls_cortex():
    engines = REPO / "macros" / "engines"
    for path in engines.glob("*.sql"):
        assert writes_in(path.read_text()) == [], path.name
    others = "\n".join(p.read_text() for p in engines.glob("*.sql") if p.name != "cortex.sql").lower()
    for forbidden in ("snowflake.ml", "anomaly_detection", "detect_anomalies"):
        assert forbidden not in others, forbidden


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
