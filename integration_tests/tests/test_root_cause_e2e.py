"""UC4 root cause wired to end_run and the views (icecore.emit_root_cause, macros/root_cause/emit_root_cause.sql).

Offline: the emitter on the synthetic graph of models/root_cause (see test_walk_upstream.py), with signals passed in,
for the three kinds. Sandbox: a real `dbt build` of the chain rce_a -> rce_b -> rce_c (models/root_cause_e2e) in an obs
schema of its own, with `root_cause: true`, where the signal is read back from the SCHEMA_CHANGES / METRICS views:

  schema_change   rce_a gains a column (a real change of its columns signature, turned into icecore.schema_change
                  events as the schema monitor will), then the not_null test on rce_c fails. The node_result LOG of
                  that test must name rce_a, kind schema_change, distance 3.
  stale_source    a freshness_lag_s anomaly recorded for rce_a (the shape emit_metric writes) is read from METRICS.

upstream_failure is covered offline: in one dbt build a failed model skips its tests, so a failed upstream and a failed
downstream test only meet across runs.
"""
import json
import time

import pytest

from helpers.macros import IT_DIR, install_deps, invoke, run_macro, run_macro_with_logs
from helpers.run_hooks import (
    DATABASE, SCHEMA, icecore_vars, invocation_records, obs_schema, run_with_hooks, snowflake_since,
)

P = "icecore_integration_tests"
SRC = f"source.{P}.rc_raw.orders"
A, B, C = (f"model.{P}.rc_{x}" for x in "abc")
RCE_A = f"model.{P}.rce_a"


@pytest.fixture(scope="module")
def test_id() -> str:
    install_deps()
    assert invoke(["parse"]).success
    manifest = json.load(open(IT_DIR / "target" / "manifest.json"))
    ids = [uid for uid, n in manifest["nodes"].items() if n["resource_type"] == "test" and n.get("attached_node") == C]
    assert len(ids) == 1, ids
    return ids[0]


def res(unique_id: str, status: str, resource_type: str = "model") -> dict:
    return {"unique_id": unique_id, "status": status,
            "node": {"unique_id": unique_id, "resource_type": resource_type}}


def log_for(unique_id: str) -> dict:
    return {"type": "log", "level": "error", "message": "x",
            "attrs": {"icecore.kind": "node_result", "dbt.node.unique_id": unique_id}}


def emit(results, items, signals):
    return run_macro("it_emit_root_cause", {"results": results, "items": items, "signals": signals})


# --- offline: the emitter on the synthetic graph ---------------------------------------------------

def test_failed_upstream_model_is_written_on_the_test_log(test_id):
    results = [res(A, "error"), res(B, "skipped"), res(C, "skipped"), res(test_id, "fail", "test")]
    items = emit(results, [log_for(test_id)], {})
    attrs = items[0]["attrs"]
    assert (attrs["dbt.root_cause.unique_id"], attrs["dbt.root_cause.kind"], attrs["dbt.root_cause.distance"]) == (
        A, "upstream_failure", 3)
    assert len(items) == 1  # annotated in place: no extra row


def test_stale_source_is_written_on_the_test_log(test_id):
    items = emit([res(test_id, "fail", "test")], [log_for(test_id)], {"freshness_breaches": {SRC: {"value": 7200}}})
    attrs = items[0]["attrs"]
    assert (attrs["dbt.root_cause.unique_id"], attrs["dbt.root_cause.kind"]) == (SRC, "stale_source")


def test_schema_change_is_written_on_the_test_log(test_id):
    signals = {"schema_diffs": {B: [{"change_kind": "added", "column": "EXTRA"}]}}
    items = emit([res(test_id, "fail", "test")], [log_for(test_id)], signals)
    attrs = items[0]["attrs"]
    assert (attrs["dbt.root_cause.unique_id"], attrs["dbt.root_cause.kind"], attrs["dbt.root_cause.distance"]) == (
        B, "schema_change", 2)


def test_without_a_test_log_the_cause_gets_a_log_of_its_own(test_id):
    items = emit([res(test_id, "fail", "test")], [], {"freshness_breaches": {SRC: True}})
    assert len(items) == 1 and items[0]["type"] == "log" and items[0]["attrs"]["icecore.kind"] == "root_cause"
    assert items[0]["attrs"]["dbt.node.unique_id"] == test_id and items[0]["attrs"]["dbt.root_cause.kind"] == "stale_source"


def test_a_passing_test_or_an_unexplained_failure_changes_nothing(test_id):
    assert emit([res(test_id, "pass", "test")], [log_for(test_id)], {"freshness_breaches": {SRC: True}}) == [log_for(test_id)]
    assert emit([res(test_id, "fail", "test")], [log_for(test_id)], {}) == [log_for(test_id)]


def test_only_tests_are_explained(test_id):
    # a failed model is not a test: nothing to explain, so no query and no change
    assert emit([res(C, "error")], [log_for(C)], {"freshness_breaches": {SRC: True}}) == [log_for(C)]


def test_root_cause_is_an_end_run_emitter_off_by_default():
    emitters = {e["name"]: e for e in run_macro("it_end_run_emitters", {})}
    assert emitters["root_cause"]["var"] == "root_cause" and emitters["root_cause"]["default"] is False
    assert list(emitters)[-1] == "root_cause"  # after node_results, whose LOGs it annotates


# --- sandbox: the signals come from the views, the cause from a real build --------------------------

@pytest.fixture
def chain(sf, run_id):
    yield run_id
    for m in ("a", "b", "c"):
        sf.cursor().execute(f'drop view if exists "{DATABASE}"."{SCHEMA}"."RCE_{m.upper()}_{run_id.upper()}"')


def failed_test_log(sf, schema, run_results, since):
    invocation_id = run_results["metadata"]["invocation_id"]
    rows = invocation_records(sf, schema, invocation_id, min_rows=4, since=since)
    logs = [r for r in rows if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "node_result"
            and r["record_attributes"].get("dbt.node.unique_id", "").startswith("test.")]
    assert len(logs) == 1, [r["record_attributes"] for r in rows]
    assert logs[0]["record_attributes"]["dbt.result.status"] == "fail"
    return logs[0]["record_attributes"]


def view_rows(sf, schema, view, where, params, timeout_s=240):
    """Poll an obs view until `where` matches (events land after 8 to 20 s, S3)."""
    deadline = time.monotonic() + timeout_s
    while True:
        rows = sf.cursor().execute(f'select * from "{DATABASE}"."{schema}"."{view}" where {where}', params).fetchall()
        if rows or time.monotonic() > deadline:
            return rows
        time.sleep(5)


@pytest.mark.sandbox
def test_upstream_schema_change_is_named_as_the_root_cause_of_a_failed_test(sf, chain):
    run_id = chain
    with obs_schema(sf) as schema:
        run_macro_with_logs("it_create_views", {"schema": schema}, ["--vars", json.dumps(icecore_vars(schema))])
        ident = f"rce_a_{run_id}"
        sig = {"database": DATABASE, "schema": SCHEMA, "identifier": ident}
        res1, _ = run_with_hooks(["build", "--select", "rce_a"], run_id, schema, root_cause=False, telemetry=False)
        assert res1.success, res1.exception
        before = run_macro("it_schema", sig)["signature"]
        res2, _ = run_with_hooks(["build", "--select", "rce_a"], run_id, schema, extra_vars={"rce_extra_col": True},
                                 root_cause=False, telemetry=False)
        assert res2.success, res2.exception
        after = run_macro("it_schema", sig)["signature"]
        assert [c["name"] for c in after] == ["ID", "EXTRA"] and [c["name"] for c in before] == ["ID"]
        run_macro("it_emit_schema_diff", {"previous": before, "current": after, "unique_id": RCE_A,
                                          "relation": f'"{DATABASE}"."{SCHEMA}"."{ident.upper()}"'},
                  ["--vars", json.dumps(icecore_vars(schema))])
        assert view_rows(sf, schema, "SCHEMA_CHANGES", "node_unique_id = %s", (RCE_A,)), "schema change never landed"

        since = snowflake_since(sf)
        res3, run_results = run_with_hooks(["build", "--select", "rce_b+"], run_id, schema,
                                           extra_vars={"rce_extra_col": True}, root_cause=True)
        assert not res3.success  # the not_null test on rce_c fails
        attrs = failed_test_log(sf, schema, run_results, since)
        assert attrs["dbt.root_cause.unique_id"] == RCE_A, attrs
        assert attrs["dbt.root_cause.kind"] == "schema_change", attrs
        assert attrs["dbt.root_cause.distance"] == 3, attrs


@pytest.mark.sandbox
def test_upstream_freshness_breach_in_metrics_is_named_as_stale_source(sf, chain):
    run_id = chain
    with obs_schema(sf) as schema:
        vars_ = ["--vars", json.dumps(icecore_vars(schema))]
        run_macro_with_logs("it_create_views", {"schema": schema}, vars_)
        res1, _ = run_with_hooks(["build", "--select", "rce_a"], run_id, schema, root_cause=False, telemetry=False)
        assert res1.success, res1.exception
        ident = f"rce_a_{run_id}"
        run_macro("it_emit_metric", {"rel": {"database": DATABASE, "schema": SCHEMA, "identifier": ident},
                                     "metric": "freshness_lag_s", "value": 7200,
                                     "anomaly": {"engine": "native", "expected": 60, "lower": 0, "upper": 300,
                                                 "score": 9.5, "is_anomaly": True}}, vars_)
        assert view_rows(sf, schema, "METRICS", "metric = 'freshness_lag_s' and anomaly_is_anomaly",
                         ()), "the breach never landed"

        since = snowflake_since(sf)
        res3, run_results = run_with_hooks(["build", "--select", "rce_b+"], run_id, schema, root_cause=True)
        assert not res3.success
        attrs = failed_test_log(sf, schema, run_results, since)
        assert attrs["dbt.root_cause.unique_id"] == RCE_A and attrs["dbt.root_cause.kind"] == "stale_source", attrs


@pytest.mark.sandbox
def test_root_cause_off_writes_no_root_cause_keys(sf, chain):
    run_id = chain
    with obs_schema(sf) as schema:
        since = snowflake_since(sf)
        res1, run_results = run_with_hooks(["build", "--select", "rce_a+"], run_id, schema)
        assert not res1.success
        attrs = failed_test_log(sf, schema, run_results, since)
        assert not [k for k in attrs if k.startswith("dbt.root_cause")], attrs
