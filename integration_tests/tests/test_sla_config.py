"""icecore.collect_sla_config: SLA and alert settings read from model meta (UC6, offline, no Snowflake).

The valid cases read the real graph built from integration_tests/models/control/_sla_fixtures.yml.
The invalid cases pass synthetic nodes, so a broken fixture never breaks the rest of the suite.
"""
import json
import math

import pytest

from helpers.macros import IT_DIR, MacroFailed, call_macro, install_deps

REPO = IT_DIR.parent


@pytest.fixture(scope="module", autouse=True)
def deps():
    install_deps()


@pytest.fixture(scope="module")
def from_graph() -> dict:
    """collect_sla_config() over the integration project's graph, keyed by model name."""
    return {entry["name"]: entry for entry in call_macro("collect_sla_config")}


def node(name: str, meta: dict | None = None, config_meta: dict | None = None) -> dict:
    return {
        "unique_id": f"model.shop.{name}",
        "name": name,
        "resource_type": "model",
        "meta": meta or {},
        "config": {"meta": config_meta or {}},
    }


def collect(*nodes: dict) -> list:
    return call_macro("collect_sla_config", nodes=list(nodes))


# --- acceptance: 1h / 15m -> 60 minutes with 15 minutes grace -------------------------------------

def test_hourly_sla_is_60_minutes_with_15_minutes_grace(from_graph):
    entry = from_graph["sla_hourly"]
    assert entry["unique_id"] == "model.icecore_integration_tests.sla_hourly"
    assert entry["resource_type"] == "model"
    assert entry["expected_every_minutes"] == 60
    assert entry["grace_minutes"] == 15
    assert entry["grace_defaulted"] is False


def test_alerts_channel_and_severity_are_read(from_graph):
    assert from_graph["sla_hourly"]["channel"] == "data-platform"
    assert from_graph["sla_hourly"]["severity"] == "warn"
    assert from_graph["sla_alerts_only"]["channel"] == "ops-oncall"
    assert from_graph["sla_alerts_only"]["severity"] == "error"


def test_alerts_without_sla_have_no_schedule(from_graph):
    entry = from_graph["sla_alerts_only"]
    assert entry["expected_every_minutes"] is None
    assert entry["grace_minutes"] is None


def test_models_without_sla_or_alerts_are_left_out(from_graph):
    assert "sla_none" not in from_graph
    assert all(name.startswith("sla_") for name in from_graph), "only the fixtures declare meta.sla / meta.alerts"


def test_daily_sla_is_1440_minutes(from_graph):
    assert from_graph["sla_daily_default_grace"]["expected_every_minutes"] == 24 * 60


# --- acceptance: no grace -> the default grace is applied ------------------------------------------

def test_missing_grace_gets_the_default(from_graph):
    default = call_macro("_sla_default_grace_minutes")
    assert isinstance(default, int) and default > 0
    entry = from_graph["sla_daily_default_grace"]
    assert entry["grace_minutes"] == default
    assert entry["grace_defaulted"] is True


def test_default_grace_is_twice_the_s3_p95_lag():
    """Spec section 10: the default grace is 2 x the p95 ingestion lag measured by spike S3, rounded up
    to a whole minute (spikes/S3.md; spikes/results/s3.json, key burst.<dbt version>, lag.p95_s)."""
    results = json.loads((REPO / "spikes" / "results" / "s3.json").read_text())
    p95 = max(v["lag"]["p95_s"] for k, v in results.items() if k.startswith("burst."))  # one burst per dbt version
    assert call_macro("_sla_default_grace_minutes") == math.ceil(2 * p95 / 60)


def test_alerts_severity_defaults_to_warn():
    [entry] = collect(node("fct_orders", meta={"alerts": {"channel": "data-platform"}}))
    assert entry["severity"] == "warn"


# --- duration grammar -------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, minutes",
    [("15m", 15), ("1h", 60), ("1h30m", 90), ("2d", 2880), ("1w", 10080), (" 45M ", 45), ("90m", 90)],
)
def test_duration_forms(text, minutes):
    [entry] = collect(node("fct_orders", meta={"sla": {"expected_every": text, "grace": "5m"}}))
    assert entry["expected_every_minutes"] == minutes


def test_top_level_meta_wins_over_config_meta():
    """Only matters for nodes built by hand: dbt merges both into node.meta and node.config.meta."""
    [entry] = collect(node("fct_orders", meta={"sla": {"expected_every": "2h"}}, config_meta={"sla": {"expected_every": "1h"}}))
    assert entry["expected_every_minutes"] == 120


def test_result_is_sorted_by_unique_id():
    entries = collect(node("b", meta={"sla": {"expected_every": "1h"}}), node("a", meta={"sla": {"expected_every": "1h"}}))
    assert [e["name"] for e in entries] == ["a", "b"]


# --- acceptance: invalid duration -> a clear compile error naming the model ------------------------

@pytest.mark.parametrize("bad", ["1 hour", "15x", "h", "", "0m", "1h0", "-5m", "1.5h", 60])
def test_invalid_expected_every_names_the_model(bad):
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": {"expected_every": bad, "grace": "15m"}}))
    message = str(err.value)
    assert "fct_orders" in message
    assert "meta.sla.expected_every" in message
    assert "15m, 1h, 1h30m" in message, "the error says what a valid duration looks like"


def test_invalid_grace_names_the_model():
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": {"expected_every": "1h", "grace": "soon"}}))
    assert "fct_orders" in str(err.value) and "meta.sla.grace" in str(err.value)


def test_sla_without_expected_every_names_the_model():
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": {"grace": "15m"}}))
    assert "fct_orders" in str(err.value) and "expected_every" in str(err.value)


def test_invalid_severity_names_the_model():
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"alerts": {"channel": "x", "severity": "critical"}}))
    assert "fct_orders" in str(err.value) and "meta.alerts.severity" in str(err.value)


def test_sla_that_is_not_a_mapping_names_the_model():
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": "1h"}))
    assert "fct_orders" in str(err.value) and "meta.sla" in str(err.value)


def test_unknown_sla_key_names_the_model():
    """A typo such as expectedEvery must not silently disable silence detection."""
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": {"expectedEvery": "1h"}}))
    assert "fct_orders" in str(err.value) and "expectedEvery" in str(err.value)


def test_error_message_reads_well():
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": {"expected_every": "1 hour"}}))
    assert (
        "icecore: model 'fct_orders' (model.shop.fct_orders): meta.sla.expected_every '1 hour' is not a valid duration"
        in str(err.value)
    )


def test_unknown_alerts_key_names_the_model():
    """Review round 1: a typo such as severty must not silently fall back to severity warn."""
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"alerts": {"channel": "c1", "severty": "error"}}))
    message = str(err.value)
    assert "fct_orders" in message and "meta.alerts" in message and "severty" in message


def test_grace_longer_than_expected_every_is_an_error():
    with pytest.raises(MacroFailed) as err:
        collect(node("fct_orders", meta={"sla": {"expected_every": "15m", "grace": "1h"}}))
    message = str(err.value)
    assert "fct_orders" in message and "meta.sla.grace" in message


def test_grace_equal_to_expected_every_is_allowed():
    [entry] = collect(node("fct_orders", meta={"sla": {"expected_every": "1h", "grace": "60m"}}))
    assert entry["grace_minutes"] == 60


def test_default_grace_never_exceeds_expected_every():
    # The shortest schedule is 1m; the default grace (1 minute since S3) is clamped to it if ever larger.
    default = call_macro("_sla_default_grace_minutes")
    [entry] = collect(node("fct_orders", meta={"sla": {"expected_every": "1m"}}))
    assert entry["grace_minutes"] == min(default, 1) and entry["grace_defaulted"] is True
