"""UC1 lineage edges: emit_lineage (macros/telemetry/emit_lineage.sql), an end-of-run emitter of end_run behind
var icecore.lineage (default true).

One LOG per part, `icecore.kind = lineage`, with the edges as a JSON list of [parent, child] unique ids in
`dbt.lineage.edges`. Edges come from graph.nodes depends_on for models, seeds, snapshots and sources; tests are
left out. Edges that do not fit payload_cap_kb are split across records (`dbt.lineage.part` of `parts`), none lost.

Offline tests render through integration_tests/macros/test_support/it_lineage.sql and it_hooks.sql. The sandbox
test runs a real `dbt build` of models/hooks against an obs schema of its own (helpers/run_hooks.py).
"""
import json

import pytest
import yaml

from helpers.macros import IT_DIR, run_macro
from helpers.run_hooks import (
    assert_no_invocation_records, drop_fixture_views, invocation_records, obs_schema, run_with_hooks, snowflake_since,
)

KB = 1024
PROJECT = "icecore_integration_tests"


def with_vars(icecore_vars: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": icecore_vars})]


def lineage_logs(records):
    return [r for r in records if r.get("type") == "log" and r["attrs"].get("icecore.kind") == "lineage"]


def edges_of(logs):
    return [tuple(edge) for log in logs for edge in json.loads(log["attrs"]["dbt.lineage.edges"])]


def synthetic_edges(n):
    return [[f"model.{PROJECT}.parent_model_with_a_long_name_{i:05d}", f"model.{PROJECT}.child_model_with_a_long_name_{i:05d}"]
            for i in range(n)]


# --- offline --------------------------------------------------------------------------------------

def test_emitter_is_listed_behind_var_lineage_default_true():
    emitters = {e["name"]: e for e in run_macro("it_end_run_emitters", {})}
    assert emitters["lineage"]["var"] == "lineage" and emitters["lineage"]["default"] is True


def test_graph_edges_have_parent_and_child_unique_ids():
    edges = run_macro("it_lineage_edges", {})
    assert [f"model.{PROJECT}.hooks_a", f"model.{PROJECT}.hooks_b"] in edges
    assert [f"model.{PROJECT}.hooks_b", f"model.{PROJECT}.hooks_c"] in edges
    assert edges == sorted(edges) and len({tuple(e) for e in edges}) == len(edges)


def test_edges_are_between_models_seeds_snapshots_and_sources_only():
    edges = run_macro("it_lineage_edges", {})
    allowed = ("model.", "seed.", "snapshot.", "source.")
    assert all(p.startswith(allowed) and c.startswith(allowed) for p, c in edges), \
        [e for e in edges if not all(x.startswith(allowed) for x in e)]
    # tests (test.*) hang off models but are not lineage edges
    assert not any(x.startswith("test.") for e in edges for x in e)


def test_end_run_batch_carries_the_edge_of_a_run():
    records = run_macro("it_end_run_render", {"results": []})["records"]
    logs = lineage_logs(records)
    assert (f"model.{PROJECT}.hooks_a", f"model.{PROJECT}.hooks_b") in edges_of(logs)
    log = logs[0]
    assert log["level"] == "info" and log["attrs"]["dbt.invocation_id"]
    assert log["attrs"]["dbt.lineage.part"] == 1 and log["attrs"]["dbt.lineage.parts"] == len(logs)
    assert log["attrs"]["dbt.lineage.edges_total"] == len(edges_of(logs))
    assert records[-1]["attrs"]["icecore.kind"] == "run_end"  # the invocation-end LOG stays last


def test_one_record_when_edges_fit():
    out = run_macro("it_lineage_render", {"edges": synthetic_edges(50)})
    assert len(out["records"]) == 1
    assert len(edges_of(out["records"])) == 50


def test_more_edges_than_the_cap_are_split_and_none_is_lost():
    edges = synthetic_edges(3000)
    out = run_macro("it_lineage_render", {"edges": edges})
    logs = out["records"]
    assert len(logs) >= 4
    assert edges_of(logs) == [tuple(e) for e in edges]  # nothing lost, nothing duplicated, order kept
    assert all(size <= 64 * KB for size in out["sizes"]), out["sizes"]
    assert not any("icecore.truncated" in log["attrs"] for log in logs)
    assert [log["attrs"]["dbt.lineage.part"] for log in logs] == list(range(1, len(logs) + 1))
    assert {log["attrs"]["dbt.lineage.parts"] for log in logs} == {len(logs)}
    assert {log["attrs"]["dbt.lineage.edges_total"] for log in logs} == {3000}
    assert sum(log["attrs"]["dbt.lineage.edge_count"] for log in logs) == 3000


def test_split_follows_payload_cap_kb():
    edges = synthetic_edges(600)
    default = run_macro("it_lineage_render", {"edges": edges})["records"]
    small = run_macro("it_lineage_render", {"edges": edges}, with_vars({"payload_cap_kb": 8}))
    assert len(small["records"]) > len(default)
    assert edges_of(small["records"]) == [tuple(e) for e in edges]
    assert all(size <= 8 * KB for size in small["sizes"])


def test_no_edges_no_record():
    assert run_macro("it_lineage_render", {"edges": []})["records"] == []


def test_lineage_false_leaves_the_batch_without_lineage():
    records = run_macro("it_end_run_render", {"results": []}, with_vars({"lineage": False}))["records"]
    assert not lineage_logs(records)


def test_telemetry_false_renders_nothing():
    out = run_macro("it_hooks_render", {"results": []}, with_vars({"telemetry": False}))
    assert out["end_run"] == ""


def test_project_vars_and_docs():
    cfg = yaml.safe_load(open(IT_DIR.parent / "dbt_project.yml"))
    assert cfg["vars"]["icecore"]["lineage"] is True
    docs = yaml.safe_load(open(IT_DIR.parent / "macros/telemetry/emit_lineage.yml"))
    assert [m["name"] for m in docs["macros"]] == ["emit_lineage"]
    assert "{{" not in (IT_DIR.parent / "macros/telemetry/emit_lineage.yml").read_text()


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture
def views(sf, run_id):
    yield run_id
    drop_fixture_views(sf, run_id)


@pytest.mark.sandbox
def test_build_records_the_edge_stg_to_fct(sf, obs, views):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], views, obs)
    assert res.success, res.exception
    rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=3, since=since)
    logs = [r for r in rows if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "lineage"]
    assert logs, [(r["record_type"], r["record_attributes"].get("icecore.kind")) for r in rows]
    assert logs[0]["scope"]["name"] == "icecore" and logs[0]["record"]["severity_text"] == "INFO"
    edges = [tuple(e) for log in logs for e in json.loads(log["record_attributes"]["dbt.lineage.edges"])]
    assert (f"model.{PROJECT}.hooks_a", f"model.{PROJECT}.hooks_b") in edges
    assert (f"model.{PROJECT}.hooks_b", f"model.{PROJECT}.hooks_c") in edges


@pytest.mark.sandbox
def test_telemetry_false_lands_no_lineage_record(sf, obs, views):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_a"], views, obs, telemetry=False)
    assert res.success, res.exception
    assert_no_invocation_records(sf, obs, run_results["metadata"]["invocation_id"], since=since)
