"""UC1 end-to-end write path: one real `dbt build` through icecore's hooks into an obs schema of its own, then
an assertion for every record kind and the keys spec section 4 gives it (docs/design/scope-and-architecture.md,
"Event-table conventions"; spikes/GATE1.md).

The build selects the hook fixtures: hooks_a -> hooks_b -> hooks_c (with the accepted_values test that warns),
hooks_dup with its unique test on id (fails, 10 stored failures) and the singular test hooks_bad_literal (errors
with a data literal in Snowflake's message), with samples on. The records read back are:

  start            SPAN_EVENT icecore.run_start, scope global, written by ICECORE_EMIT at on-run-start
  invocation span  SPAN icecore.invocation
  node spans       SPAN icecore.node, one per node, with dbt's timings
  node results     LOG icecore.kind = node_result, one per node
  query events     SPAN_EVENT icecore.query, one per node query id
  lineage          LOG icecore.kind = lineage
  samples          LOG icecore.kind = sample (INFO), for the failed test with stored failures only
  end              LOG icecore.kind = run_end, with the status and node counts

Run on both dbt versions: `.venv/bin/pytest -m sandbox tests/test_write_path.py` with the floor and the latest venv.
"""
import json
import uuid

import pytest
import yaml

from helpers.macros import IT_DIR
from helpers.run_hooks import (
    DATABASE, drop_fixture_views, drop_stored_failures, invocation_records, node_results, obs_schema, run_with_hooks,
    snowflake_since,
)

PROJECT = "icecore_integration_tests"
OTHER_DUP_TESTS = ["hooks_dup_unique_code", "hooks_dup_code_not_null", "hooks_dup_unique_big"]
BUILD_ARGS = ["build", "--select", "hooks_a+", "hooks_dup", "hooks_bad_literal", "--exclude", *OTHER_DUP_TESTS]

# Spec section 4 keys, per record kind.
BASE_KEYS = ["dbt.invocation_id", "dbt.runtime", "dbt.command", "dbt.target", "dbt.project", "icecore.version",
             "icecore.schema_version"]
RUN_KEYS = ["dbt.version", "icecore.anomaly_engine", "icecore.shadow_engine"]
NODE_KEYS = ["dbt.node.unique_id", "dbt.node.type", "dbt.node.name"]
TEST_KEYS = ["dbt.test.name", "dbt.test.type", "dbt.test.severity"]  # a generic test also has dbt.test.column
RESULT_KEYS = ["dbt.result.status", "dbt.result.execution_time_s", "dbt.result.message"]


# --- offline --------------------------------------------------------------------------------------

def test_fixtures_are_tagged_out_of_the_default_selector():
    selectors = yaml.safe_load(open(IT_DIR / "selectors.yml"))
    excluded = [e for e in selectors["selectors"][0]["definition"]["union"] if "exclude" in e][0]["exclude"]
    assert {"method": "tag", "value": "icecore_hooks"} in excluded
    assert "icecore_hooks" in (IT_DIR / "tests_dbt" / "hooks_bad_literal.sql").read_text()


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture(scope="module")
def build(sf, obs):
    """One build with samples on; the records of its invocation, read after they settle."""
    run_id = uuid.uuid4().hex
    since = snowflake_since(sf)
    try:
        res, run_results = run_with_hooks(BUILD_ARGS, run_id, obs, samples=True)
        assert not res.success  # hooks_bad_literal errors and hooks_dup_unique_id fails, on purpose
        nodes = node_results(run_results)
        queries = sum(1 for r in nodes if r["adapter_response"].get("query_id"))
        # start + invocation span + end + lineage + sample, node spans, node results and query events
        rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"],
                                  min_rows=5 + 2 * len(nodes) + queries, since=since)
    finally:
        drop_fixture_views(sf, run_id)
        drop_stored_failures(sf, run_id)
    return {"run_results": run_results, "nodes": nodes, "rows": rows,
            "invocation_id": run_results["metadata"]["invocation_id"]}


def of(rows, record_type, name=None, kind=None):
    out = [r for r in rows if r["record_type"] == record_type]
    if name is not None:
        out = [r for r in out if r["record"].get("name") == name]
    if kind is not None:
        out = [r for r in out if r["record_attributes"].get("icecore.kind") == kind]
    return out


def has_keys(record, keys):
    missing = [k for k in keys if k not in record["record_attributes"]]
    assert not missing, (record["record_type"], record["record"].get("name"), missing)


def by_node(records):
    return {r["record_attributes"]["dbt.node.unique_id"]: r for r in records}


@pytest.mark.sandbox
def test_the_build_ran_the_nodes_it_was_meant_to(build):
    statuses = sorted(r["status"] for r in build["nodes"])
    assert statuses.count("success") == 4 and "warn" in statuses and "fail" in statuses and "error" in statuses


@pytest.mark.sandbox
def test_every_record_is_identified_and_carries_the_base_keys(sf, build):
    for row in build["rows"]:
        assert row["record_attributes"]["dbt.invocation_id"] == build["invocation_id"]
        has_keys(row, BASE_KEYS)
        assert row["record_attributes"]["dbt.command"] == "build"
        assert row["record_attributes"]["dbt.runtime"] == "core"
        assert row["record_attributes"]["icecore.schema_version"] == 1
        assert row["resource_attributes"]["snow.database.name"] == DATABASE
        assert row["resource_attributes"]["snow.executable.name"].startswith("ICECORE_EMIT")


@pytest.mark.sandbox
def test_start_record(build):
    start, = of(build["rows"], "SPAN_EVENT", "icecore.run_start")
    has_keys(start, RUN_KEYS + ["dbt.invocation.started_at"])
    assert start["scope"]["name"] == "global" and start["resource_attributes"]["snow.executable.name"].startswith("ICECORE_EMIT(")
    assert start["record_attributes"]["icecore.message"] == "dbt build started"


@pytest.mark.sandbox
def test_invocation_span(build):
    span, = of(build["rows"], "SPAN", "icecore.invocation")
    has_keys(span, RUN_KEYS + ["dbt.invocation.started_at", "dbt.invocation.completed_at", "dbt.invocation.duration_s"])
    assert span["scope"]["name"] == "icecore" and span["resource_attributes"]["snow.executable.name"].startswith("ICECORE_EMIT_RUN(")
    assert span["record_attributes"]["dbt.invocation.duration_s"] >= 0


@pytest.mark.sandbox
def test_node_spans_one_per_node(build):
    spans = by_node(of(build["rows"], "SPAN", "icecore.node"))
    assert sorted(spans) == sorted(r["unique_id"] for r in build["nodes"])
    for unique_id, span in spans.items():
        has_keys(span, NODE_KEYS + ["dbt.node.started_at", "dbt.node.completed_at", "dbt.node.duration_s"])
        assert span["scope"]["name"] == "icecore"
        if unique_id.startswith("model."):
            has_keys(span, ["dbt.node.materialization", "dbt.node.relation"])
            assert span["record_attributes"]["dbt.node.relation"].count(".") == 2
        if unique_id.startswith("test."):
            has_keys(span, TEST_KEYS + ([] if "hooks_bad_literal" in unique_id else ["dbt.test.column"]))


@pytest.mark.sandbox
def test_node_results_one_per_node(build):
    logs = by_node(of(build["rows"], "LOG", kind="node_result"))
    assert sorted(logs) == sorted(r["unique_id"] for r in build["nodes"])
    levels = {"success": "INFO", "pass": "INFO", "warn": "WARN", "fail": "ERROR", "error": "ERROR"}
    for res in build["nodes"]:
        log = logs[res["unique_id"]]
        has_keys(log, NODE_KEYS + ["dbt.result.status", "dbt.result.execution_time_s"])
        assert log["record_attributes"]["dbt.result.status"] == res["status"]
        assert log["record"]["severity_text"] == levels[res["status"]]
        assert log["scope"]["name"] == "icecore"
        if res["unique_id"].startswith("model."):
            has_keys(log, ["dbt.node.materialization", "dbt.node.relation", "snow.query.id"])
        if res["unique_id"].startswith("test."):
            has_keys(log, TEST_KEYS + ([] if "hooks_bad_literal" in res["unique_id"] else ["dbt.test.column"]))


@pytest.mark.sandbox
def test_test_with_failures_has_its_count_and_a_redacted_message(build):
    logs = by_node(of(build["rows"], "LOG", kind="node_result"))
    failed = next(u for u in logs if ".hooks_dup_unique_id." in u)
    a = logs[failed]["record_attributes"]
    assert a["dbt.result.status"] == "fail" and a["dbt.test.failures"] == 10
    assert a["dbt.test.name"] == "unique" and a["dbt.test.severity"] == "ERROR"
    assert a["dbt.result.message"] == "Got 10 results, configured to fail if != 0"
    # an erroring test: Snowflake's message quotes a data literal, which must not reach the table
    errored = next(u for u in logs if u.startswith("test.") and "hooks_bad_literal" in u)
    message = logs[errored]["record_attributes"]["dbt.result.message"]
    assert "Numeric value '?' is not recognized" in message and "secret_value" not in message
    assert "secret_value" not in json.dumps([r["record"] for r in build["rows"]])
    assert "secret_value" not in json.dumps([r["record_attributes"] for r in build["rows"]])


@pytest.mark.sandbox
def test_query_events_match_the_run_results(build):
    events = of(build["rows"], "SPAN_EVENT", "icecore.query")
    got = {e["record_attributes"]["dbt.node.unique_id"]: e["record_attributes"]["snow.query.id"] for e in events}
    expected = {r["unique_id"]: r["adapter_response"]["query_id"] for r in build["nodes"]
                if r["adapter_response"].get("query_id")}
    assert got == expected and len(events) == len(expected) > 0


@pytest.mark.sandbox
def test_lineage_edges(build):
    logs = of(build["rows"], "LOG", kind="lineage")
    assert logs and all(r["record"]["severity_text"] == "INFO" and r["scope"]["name"] == "icecore" for r in logs)
    for log in logs:
        has_keys(log, ["dbt.lineage.edges", "dbt.lineage.edge_count", "dbt.lineage.edges_total",
                       "dbt.lineage.part", "dbt.lineage.parts"])
    edges = [tuple(e) for log in logs for e in json.loads(log["record_attributes"]["dbt.lineage.edges"])]
    assert len(edges) == len(set(edges)) == logs[0]["record_attributes"]["dbt.lineage.edges_total"]
    assert (f"model.{PROJECT}.hooks_a", f"model.{PROJECT}.hooks_b") in edges
    assert (f"model.{PROJECT}.hooks_b", f"model.{PROJECT}.hooks_c") in edges


@pytest.mark.sandbox
def test_sample_for_the_failed_test_only(build):
    samples = of(build["rows"], "LOG", kind="sample")
    sample, = samples  # the warn test, the errored test and the passing ones have none
    a = sample["record_attributes"]
    has_keys(sample, NODE_KEYS + TEST_KEYS + ["dbt.test.column", "dbt.test.failures", "dbt.sample.rows", "dbt.sample.row_count",
                                               "dbt.sample.truncated"])
    assert ".hooks_dup_unique_id." in a["dbt.node.unique_id"]
    assert sample["record"]["severity_text"] == "INFO" and sample["scope"]["name"] == "icecore"
    rows = json.loads(a["dbt.sample.rows"])
    assert len(rows) == a["dbt.sample.row_count"] == 10 and a["dbt.sample.truncated"] is False
    assert sorted(int(r["UNIQUE_FIELD"]) for r in rows) == list(range(1, 11))


@pytest.mark.sandbox
def test_end_log_has_the_status_and_counts(build):
    end, = of(build["rows"], "LOG", kind="run_end")
    a = end["record_attributes"]
    has_keys(end, RUN_KEYS + ["dbt.invocation.status", "dbt.invocation.started_at", "dbt.invocation.completed_at",
                              "dbt.invocation.duration_s"])
    n = build["nodes"]
    assert a["dbt.invocation.status"] == "error" and end["record"]["severity_text"] == "ERROR"
    assert a["dbt.invocation.nodes_total"] == len(n)
    assert a["dbt.invocation.nodes_failed"] == sum(1 for r in n if r["status"] in ("error", "fail"))
    assert a["dbt.invocation.nodes_warned"] == sum(1 for r in n if r["status"] == "warn")
    assert a["dbt.invocation.nodes_success"] == sum(1 for r in n if r["status"] in ("success", "pass"))
    assert a["dbt.invocation.nodes_skipped"] == 0


@pytest.mark.sandbox
def test_nothing_else_landed(build):
    kinds = {(r["record_type"], r["record"].get("name"), r["record_attributes"].get("icecore.kind")) for r in build["rows"]}
    assert kinds == {("SPAN_EVENT", "icecore.run_start", None), ("SPAN", "icecore.invocation", None),
                     ("SPAN", "icecore.node", None), ("SPAN_EVENT", "icecore.query", None),
                     ("LOG", None, "node_result"), ("LOG", None, "lineage"), ("LOG", None, "sample"),
                     ("LOG", None, "run_end")}
