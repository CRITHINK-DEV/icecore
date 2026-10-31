"""icecore.walk_upstream on the synthetic graph in models/root_cause (offline, no Snowflake).

Graph (all nodes tagged root_cause_fixture and left out of a plain dbt build):

    source rc_raw.orders -> rc_a -> rc_b -> rc_c -> not_null test on rc_c.id
                             rc_a -> rc_e
    rc_d <- rc_b, rc_e (diamond over rc_a) and rc_snap (snapshot) <- rc_seed (seed)
"""
import json

import pytest

from helpers.operations import IT_DIR, install_deps, invoke, run_macro

P = "icecore_integration_tests"
SRC = "source.icecore_integration_tests.rc_raw.orders"
A, B, C, D, E = (f"model.{P}.rc_{x}" for x in "abcde")
SNAP = f"snapshot.{P}.rc_snap"
SEED = f"seed.{P}.rc_seed"
FIXTURE_TAG = "root_cause_fixture"


@pytest.fixture(scope="module")
def test_id() -> str:
    install_deps(offline=True)
    assert invoke(["parse"], offline=True).success
    manifest = json.load(open(IT_DIR / "target" / "manifest.json"))
    ids = [uid for uid, n in manifest["nodes"].items()
           if n["resource_type"] == "test" and n.get("attached_node") == C]
    assert len(ids) == 1, ids
    return ids[0]


def walk(unique_id, results=None, signals=None):
    args = {"unique_id": unique_id, "results": results or [], "signals": signals or {}}
    return run_macro("it_walk_upstream", args, offline=True)["cause"]


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


def test_diamond_reaches_the_shared_ancestor_once(test_id):
    results = statuses(**{A: "error", B: "skipped", E: "skipped", D: "skipped"})
    assert walk(D, results) == {"unique_id": A, "kind": "upstream_failure", "distance": 2}


def test_snapshot_parent_is_a_cause(test_id):
    assert walk(D, statuses(**{SNAP: "error"})) == {"unique_id": SNAP, "kind": "upstream_failure", "distance": 1}


def test_seed_grandparent_is_a_cause(test_id):
    results = statuses(**{SEED: "error", SNAP: "skipped"})
    assert walk(D, results) == {"unique_id": SEED, "kind": "upstream_failure", "distance": 2}


def test_equal_distance_ties_go_to_kind_precedence(test_id):
    signals = {"freshness_breaches": {SNAP: True}, "schema_diffs": {B: [{"change_kind": "removed"}]}}
    results = statuses(**{E: "error"})
    assert walk(D, results, signals) == {"unique_id": E, "kind": "upstream_failure", "distance": 1}
    assert walk(D, [], signals) == {"unique_id": B, "kind": "schema_change", "distance": 1}


def test_prepared_status_map_is_accepted(test_id):
    assert walk(test_id, {A: "error"}) == {"unique_id": A, "kind": "upstream_failure", "distance": 3}


def test_fixtures_are_left_out_of_default_selection(test_id):
    """A plain dbt build/test must not touch the fixtures: rc_raw.orders does not exist."""
    tagged = invoke(["ls", "--select", f"tag:{FIXTURE_TAG}", "--resource-type", "all", "--output", "name"], offline=True)
    assert tagged.success and len(tagged.result) == 9, tagged.result  # 5 models, test, seed, snapshot, source
    default = invoke(["ls", "--resource-type", "all", "--output", "name"], offline=True)
    assert default.success
    assert not [name for name in default.result if "rc_" in name], default.result


@pytest.mark.sandbox
def test_plain_dbt_build_passes_in_the_sandbox(dbt):
    assert dbt(["build"]).success
