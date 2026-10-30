"""icecore.walk_upstream on the synthetic graph in models/root_cause (offline, no Snowflake).

Graph: source rc_raw.orders -> rc_a -> rc_b -> rc_c -> not_null test on rc_c.id
"""
import json

import pytest

from helpers.operations import IT_DIR, _invoke, install_deps, run_operation_json

P = "icecore_integration_tests"
SRC = "source.icecore_integration_tests.rc_raw.orders"
A, B, C = (f"model.{P}.rc_{x}" for x in "abc")


@pytest.fixture(scope="module")
def test_id() -> str:
    install_deps()
    assert _invoke(["parse"]).success
    manifest = json.load(open(IT_DIR / "target" / "manifest.json"))
    ids = [uid for uid, n in manifest["nodes"].items()
           if n["resource_type"] == "test" and n.get("attached_node") == C]
    assert len(ids) == 1, ids
    return ids[0]


def walk(unique_id, results=None, signals=None):
    args = {"unique_id": unique_id, "results": results or [], "signals": signals or {}}
    return run_operation_json("it_walk_upstream", args)["cause"]


def statuses(**by_id):
    return [{"unique_id": uid, "status": status} for uid, status in by_id.items()]


def test_failed_grandparent_is_returned(test_id):
    results = statuses(**{A: "error", B: "skipped", C: "skipped", test_id: "fail"})
    assert walk(test_id, results) == {"unique_id": A, "kind": "upstream_failure", "distance": 3}


def test_nearest_of_two_failed_ancestors_is_returned(test_id):
    results = statuses(**{A: "error", B: "error", C: "skipped", test_id: "fail"})
    assert walk(test_id, results) == {"unique_id": B, "kind": "upstream_failure", "distance": 2}


def test_no_flagged_ancestor_returns_none(test_id):
    results = statuses(**{A: "success", B: "success", C: "success", test_id: "fail"})
    assert walk(test_id, results, {"freshness_breaches": {}, "schema_diffs": {}}) is None


def test_walk_reaches_a_stale_source(test_id):
    results = statuses(**{A: "success", B: "success", C: "success", test_id: "fail"})
    signals = {"freshness_breaches": {SRC: {"max_loaded_at": "2026-10-29T00:00:00Z"}}}
    assert walk(test_id, results, signals) == {"unique_id": SRC, "kind": "stale_source", "distance": 4}


def test_nearest_wins_over_kind(test_id):
    results = statuses(**{A: "error", B: "success", C: "success", test_id: "fail"})
    signals = {"schema_diffs": {B: [{"change_kind": "removed", "column": "ID"}]}}
    assert walk(test_id, results, signals) == {"unique_id": B, "kind": "schema_change", "distance": 2}


def test_start_node_itself_is_not_its_own_cause():
    results = statuses(**{C: "error", B: "error"})
    assert walk(C, results) == {"unique_id": B, "kind": "upstream_failure", "distance": 1}


def test_unknown_unique_id_returns_none():
    assert walk("model.nowhere.missing", statuses(**{A: "error"})) is None
