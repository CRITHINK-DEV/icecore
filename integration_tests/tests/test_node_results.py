"""UC1 node results and query events, two end-of-run emitters of end_run (macros/telemetry/end_run.sql):

- emit_node_results (emit_node_result.sql): one LOG per node, `icecore.kind = node_result`, with
  node_attributes(node, result): dbt.result.status, rows_affected, execution_time_s, message, dbt.test.failures
  for tests, snow.query.id. Behind var icecore.node_results (default true).
- emit_query_events (emit_query_event.sql): one span event `icecore.query` per query id on the node's
  `icecore.node` span, with `snow.query.id`; ids from collect_query_ids (collect_query_ids.sql), which reads
  result.adapter_response.query_id. Behind var icecore.query_events (default true).

Offline tests render end_run's batch through integration_tests/macros/test_support/it_hooks.sql. Sandbox tests
run `dbt build --select hooks_a+` and check the query ids against INFORMATION_SCHEMA.QUERY_HISTORY, in a window
opened on Snowflake's clock.
"""
import json
import time
import uuid

import pytest

from helpers.macros import run_macro
from helpers.run_hooks import (
    drop_fixture_views, invocation_records, node_results, obs_schema, run_with_hooks, snowflake_since,
)

T = [{"name": "compile", "started_at": "2026-10-31T17:00:00.000000Z", "completed_at": "2026-10-31T17:00:00.100000Z"},
     {"name": "execute", "started_at": "2026-10-31T17:00:00.100000Z", "completed_at": "2026-10-31T17:00:01.000000Z"}]


def result(unique_id, status, resource_type="model", query_id="01q-a", rows=1, failures=None, message="SUCCESS 1",
           timing=T):
    node = {"unique_id": unique_id, "name": unique_id.split(".")[-1], "resource_type": resource_type,
            "config": {"materialized": "view" if resource_type == "model" else "test", "severity": "WARN"}}
    if resource_type == "test":
        node["test_metadata"] = {"name": "accepted_values"}
        node["column_name"] = "id"
    return {"unique_id": unique_id, "status": status, "timing": timing, "execution_time": 0.9,
            "adapter_response": {"query_id": query_id, "rows_affected": rows, "_message": message} if query_id else {},
            "message": message, "failures": failures, "node": node}


MODEL = result("model.p.a", "success", query_id="01q-a")
TEST = result("test.p.accepted_values_a_id", "warn", "test", query_id="01q-t", rows=None, failures=3,
              message="Got 3 results, configured to warn if != 0")
ERROR = result("model.p.bad", "error", query_id=None, message="invalid identifier 'NO_SUCH_COLUMN'")
SKIPPED = result("model.p.after", "skipped", query_id=None, message=None, timing=[])


def batch(results, **settings):
    extra = ["--vars", json.dumps({"icecore": settings})] if settings else None
    return run_macro("it_end_run_render", {"results": results}, extra)["records"]


def node_logs(records):
    return [r for r in records if r.get("type") == "log" and r["attrs"].get("icecore.kind") == "node_result"]


def node_spans(records):
    return {r["attrs"]["dbt.node.unique_id"]: r for r in records if r.get("name") == "icecore.node"}


# --- offline ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("response,expected", [
    ({"query_id": "01a"}, ["01a"]),
    ({"query_id": ["01a", "01b", "01a", ""]}, ["01a", "01b"]),
    ({"query_id": None}, []),
    ({}, []),
    (None, []),
])
def test_collect_query_ids_reads_the_adapter_response(response, expected):
    assert run_macro("it_collect_query_ids", {"result": {"adapter_response": response}}) == expected


def test_one_node_result_per_node_with_its_result_keys():
    logs = node_logs(batch([MODEL, TEST, ERROR, SKIPPED]))
    by_id = {r["attrs"]["dbt.node.unique_id"]: r for r in logs}
    assert sorted(by_id) == sorted(r["unique_id"] for r in [MODEL, TEST, ERROR, SKIPPED])
    m = by_id["model.p.a"]
    assert m["level"] == "info" and m["message"] == "model.p.a success: SUCCESS 1"
    a = m["attrs"]
    assert a["dbt.result.status"] == "success" and a["dbt.result.rows_affected"] == 1
    assert a["dbt.result.execution_time_s"] == 0.9 and a["dbt.result.message"] == "SUCCESS 1"
    assert a["snow.query.id"] == "01q-a" and a["dbt.invocation_id"]
    assert a["dbt.node.started_at"] == T[0]["started_at"] and a["dbt.node.completed_at"] == T[1]["completed_at"]
    t = by_id["test.p.accepted_values_a_id"]
    assert t["level"] == "warn"
    assert t["attrs"]["dbt.test.failures"] == 3 and t["attrs"]["dbt.test.name"] == "accepted_values"
    assert by_id["model.p.bad"]["level"] == "error"
    assert by_id["model.p.after"]["attrs"]["dbt.result.status"] == "skipped"
    assert "dbt.node.started_at" not in by_id["model.p.after"]["attrs"]


def test_query_events_ride_on_the_node_spans():
    spans = node_spans(batch([MODEL, TEST, ERROR]))
    event, = spans["model.p.a"]["events"]
    assert event["name"] == "icecore.query"
    assert event["attrs"]["snow.query.id"] == "01q-a" and event["attrs"]["dbt.node.unique_id"] == "model.p.a"
    assert event["attrs"]["dbt.invocation_id"]
    assert spans["model.p.a"]["start_ns"] <= event["ts_ns"] <= spans["model.p.a"]["end_ns"]
    assert spans["test.p.accepted_values_a_id"]["events"][0]["attrs"]["snow.query.id"] == "01q-t"
    assert spans["model.p.bad"]["events"] == []


def test_node_results_and_query_events_are_behind_their_vars():
    records = batch([MODEL, TEST], node_results=False)
    assert node_logs(records) == [] and node_spans(records)["model.p.a"]["events"]
    records = batch([MODEL, TEST], query_events="false")
    assert len(node_logs(records)) == 2 and all(not s["events"] for s in node_spans(records).values())


def test_end_run_lists_its_emitters_with_their_vars():
    # the first two, in order; later emitters (lineage, samples...) follow
    assert run_macro("it_end_run_emitters", {})[:2] == [
        {"name": "node_results", "var": "node_results", "default": True},
        {"name": "query_events", "var": "query_events", "default": True},
    ]


SECRET = ("Database Error in model x\n  100038 (22018): Numeric value 'jane@x.com' is not recognized; "
          "column \"o'k \"\"name\"\"\" and 'it''s \\' escaped' end")


@pytest.mark.parametrize("mode,expected", [
    (None, "Database Error in model x\n  100038 (22018): Numeric value '?' is not recognized; "
           "column \"?\" and '?' end"),
    ("redacted", "Database Error in model x\n  100038 (22018): Numeric value '?' is not recognized; "
                 "column \"?\" and '?' end"),
    ("full", SECRET),
    ("none", None),
])
def test_result_message_modes(mode, expected):
    settings = {} if mode is None else {"result_message": mode}
    failed = result("model.p.bad", "error", query_id=None, message=SECRET)
    log, = node_logs(batch([failed], **settings))
    assert log["attrs"].get("dbt.result.message") == expected
    assert "jane@x.com" not in log["message"] or mode == "full"
    assert log["message"] == "model.p.bad error" + (": " + expected if expected else "")


def test_redacted_message_is_capped_at_1000_characters():
    long = "x" * 5000 + " 'secret'"
    out = run_macro("it_result_message", {"messages": [long, "value 'a' and \"b\" then 'tail data"]})
    assert out[0] == "x" * 1000
    assert out[1] == "value '?' and \"?\" then '?'"
    assert "tail data" not in out[1]


def test_package_declares_the_emitter_vars():
    import yaml
    from helpers.macros import IT_DIR
    cfg = yaml.safe_load(open(IT_DIR.parent / "dbt_project.yml"))["vars"]["icecore"]
    assert cfg["node_results"] is True and cfg["query_events"] is True
    assert cfg["result_message"] == "redacted"


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture(scope="module")
def build(sf, obs):
    run_id = uuid.uuid4().hex
    since = snowflake_since(sf)
    try:
        res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, obs)
        assert res.success, res.exception
        nodes = node_results(run_results)
        # invocation span + node spans + query events + node results + end LOG
        rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=3 * len(nodes) + 2,
                                  since=since)
    finally:
        drop_fixture_views(sf, run_id)
    return {"nodes": nodes, "rows": rows, "since": since}


def landed_node_results(rows):
    return {r["record_attributes"]["dbt.node.unique_id"]: r for r in rows
            if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "node_result"}


@pytest.mark.sandbox
def test_three_models_have_success_node_results(build):
    landed = landed_node_results(build["rows"])
    models = {u: r for u, r in landed.items() if u.startswith("model.")}
    assert len(models) == 3
    assert all(r["record_attributes"]["dbt.result.status"] == "success" for r in models.values())
    assert sorted(landed) == sorted(r["unique_id"] for r in build["nodes"])
    assert all(r["scope"]["name"] == "icecore" for r in landed.values())


@pytest.mark.sandbox
def test_test_node_result_has_failures_3(build):
    tests = [r for u, r in landed_node_results(build["rows"]).items() if u.startswith("test.")]
    assert len(tests) == 1
    a = tests[0]["record_attributes"]
    assert a["dbt.test.failures"] == 3 and a["dbt.result.status"] == "warn"
    assert tests[0]["record"]["severity_text"] == "WARN"


@pytest.mark.sandbox
def test_query_events_match_query_history(sf, build):
    events = [r for r in build["rows"] if r["record_type"] == "SPAN_EVENT" and r["record"].get("name") == "icecore.query"]
    by_node = {e["record_attributes"]["dbt.node.unique_id"]: e["record_attributes"]["snow.query.id"] for e in events}
    expected = {r["unique_id"]: r["adapter_response"].get("query_id") for r in build["nodes"]
                if r["adapter_response"].get("query_id")}
    assert by_node == expected
    assert sum(1 for u in by_node if u.startswith("model.")) == 3
    ids = sorted(set(by_node.values()))
    placeholders = ", ".join(["%s"] * len(ids))
    sql = (f"select query_id from table(information_schema.query_history("
           f"end_time_range_start => dateadd(minute, -30, current_timestamp()), result_limit => 10000))"
           f" where query_id in ({placeholders})")
    deadline, seen = time.monotonic() + 120, set()
    while True:
        seen = {row[0] for row in sf.cursor().execute(sql, ids).fetchall()}
        if seen == set(ids) or time.monotonic() > deadline:
            break
        time.sleep(5)
    assert seen == set(ids)


@pytest.mark.sandbox
def test_failing_model_result_message_is_redacted(sf, obs):
    run_id = uuid.uuid4().hex
    since = snowflake_since(sf)
    try:
        res, run_results = run_with_hooks(["build", "--select", "hooks_fail"], run_id, obs,
                                          extra_vars={"hooks_fail": True})
    finally:
        drop_fixture_views(sf, run_id)
    assert not res.success
    failed, = node_results(run_results)
    assert "'NO_SUCH_COLUMN'" in failed["message"]  # what dbt itself reports
    rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=3, since=since)
    log = landed_node_results(rows)[failed["unique_id"]]
    message = log["record_attributes"]["dbt.result.message"]
    assert "invalid identifier '?'" in message and "NO_SUCH_COLUMN" not in message and len(message) <= 1000
    assert log["value"] == f"{failed['unique_id']} error: {message}"
    assert log["record"]["severity_text"] == "ERROR"
