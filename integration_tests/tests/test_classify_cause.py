"""icecore.classify_cause on synthetic inputs, one test per cause kind (offline, no Snowflake)."""
import pytest

from helpers.operations import install_deps, run_operation_json

STG = {"unique_id": "model.shop.stg_orders", "resource_type": "model"}
SRC = {"unique_id": "source.shop.raw.orders", "resource_type": "source"}


@pytest.fixture(scope="module", autouse=True)
def deps():
    install_deps()


def classify(node, run_results=None, signals=None) -> str:
    args = {"node": node, "run_results": run_results or [], "signals": signals or {}}
    return run_operation_json("it_classify_cause", args)["kind"]


def test_ancestor_with_status_error_is_upstream_failure():
    results = [{"unique_id": STG["unique_id"], "status": "error"}]
    assert classify(STG, results) == "upstream_failure"


def test_source_with_freshness_breach_is_stale_source():
    signals = {"freshness_breaches": {SRC["unique_id"]: {"max_loaded_at": "2026-10-29T00:00:00Z"}}}
    assert classify(SRC, [], signals) == "stale_source"


def test_ancestor_with_schema_diff_is_schema_change():
    signals = {"schema_diffs": {STG["unique_id"]: [{"change_kind": "removed", "column": "AMOUNT"}]}}
    assert classify(STG, [{"unique_id": STG["unique_id"], "status": "success"}], signals) == "schema_change"


def test_healthy_node_is_none():
    signals = {"freshness_breaches": {}, "schema_diffs": {"model.shop.other": [{"change_kind": "added"}]}}
    assert classify(STG, [{"unique_id": STG["unique_id"], "status": "success"}], signals) == "none"


def test_skipped_node_is_not_a_cause():
    assert classify(STG, [{"unique_id": STG["unique_id"], "status": "skipped"}]) == "none"


def test_failure_wins_over_signals():
    signals = {"freshness_breaches": {STG["unique_id"]: True}, "schema_diffs": {STG["unique_id"]: [{"change_kind": "added"}]}}
    assert classify(STG, [{"unique_id": STG["unique_id"], "status": "error"}], signals) == "upstream_failure"


def test_schema_change_wins_over_staleness():
    signals = {"freshness_breaches": {SRC["unique_id"]: True}, "schema_diffs": {SRC["unique_id"]: [{"change_kind": "type_changed"}]}}
    assert classify(SRC, [], signals) == "schema_change"


def test_node_given_as_unique_id_and_nested_result_shape():
    results = [{"node": {"unique_id": STG["unique_id"]}, "status": "error"}]
    assert classify(STG["unique_id"], results) == "upstream_failure"


def test_prepared_status_map_is_accepted():
    """walk_upstream builds {unique_id: status} once and passes it to every classify_cause call."""
    assert classify(STG, {STG["unique_id"]: "error"}) == "upstream_failure"
    assert classify(STG, {STG["unique_id"]: "success"}) == "none"
