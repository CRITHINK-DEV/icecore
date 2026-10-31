"""UC1 timing records (macros/telemetry/spans.sql): end_run sends the invocation span and one span per node to
ICECORE_EMIT_RUN, with dbt's own times (result.timing and run_started_at), never ingestion time
(GATE1.md, "Consequences for the build"; OBSERVED_TIMESTAMP is not used, S3).

- `icecore.node` spans: START_TIMESTAMP / TIMESTAMP are the node's first timing start and last timing end, with
  `dbt.node.started_at` / `dbt.node.completed_at` and one pair per phase (`dbt.timing.<phase>.*`) as attributes.
- `icecore.invocation` span: from run_started_at to the end_run hook, with `dbt.invocation.started_at`,
  `dbt.invocation.completed_at` and `dbt.invocation.duration_s`, which the invocation-end LOG carries too.

Offline tests render through integration_tests/macros/test_support/it_hooks.sql and it_timings.sql; sandbox
tests compare what lands with target/run_results.json of the same build.
"""
import calendar
import datetime as dt
import uuid

import pytest

from helpers.macros import run_macro
from helpers.run_hooks import (
    drop_fixture_views, invocation_records, node_results, obs_schema, run_end_logs, run_with_hooks, snowflake_since,
)


def ns(text: str) -> int:
    """Nanoseconds since the epoch of a run_results.json timestamp (UTC, `Z` or no zone)."""
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return calendar.timegm(value.utctimetuple()) * 10**9 + value.microsecond * 1000


def ms_of_ntz(value: dt.datetime) -> int:
    """Milliseconds since the epoch of an event-table TIMESTAMP_NTZ (UTC)."""
    return calendar.timegm(value.utctimetuple()) * 1000 + value.microsecond // 1000


def timing(compile_start, compile_end, execute_start, execute_end) -> list[dict]:
    return [{"name": "compile", "started_at": compile_start, "completed_at": compile_end},
            {"name": "execute", "started_at": execute_start, "completed_at": execute_end}]


def result(unique_id: str, status: str, timings: list, resource_type: str = "model") -> dict:
    return {"unique_id": unique_id, "status": status, "timing": timings, "adapter_response": {},
            "node": {"unique_id": unique_id, "name": unique_id.split(".")[-1], "resource_type": resource_type,
                     "config": {"materialized": "view"}}}


A = result("model.p.a", "success", timing("2026-10-31T17:00:00.100000Z", "2026-10-31T17:00:00.250000Z",
                                          "2026-10-31T17:00:00.250100Z", "2026-10-31T17:00:01.999999Z"))
B = result("model.p.b", "success", timing("2026-10-31T17:00:02.000001Z", "2026-10-31T17:00:02.100000Z",
                                          "2026-10-31T17:00:02.100001Z", "2026-10-31T17:00:04.500000Z"))
SKIPPED = result("model.p.c", "skipped", [])
HOOK = result("operation.p.p-on-run-end-0", "success", timing("2026-10-31T17:00:05Z", "2026-10-31T17:00:05Z",
                                                              "2026-10-31T17:00:05Z", "2026-10-31T17:00:06Z"),
              "operation")


# --- offline ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2026-10-31T17:00:00.123456Z", ns("2026-10-31T17:00:00.123456Z")),
    ("2026-10-31T17:00:00.123456", ns("2026-10-31T17:00:00.123456Z")),
    ("2026-10-31T17:00:00Z", ns("2026-10-31T17:00:00Z")),
    ("2026-10-31T19:00:00.5+02:00", ns("2026-10-31T17:00:00.500000Z")),
    ("2026-10-31 17:00:00.123456789+00:00", ns("2026-10-31T17:00:00Z") + 123456789),
    ("not a time", None),
])
def test_epoch_ns_reads_run_results_timestamps(text, expected):
    assert run_macro("it_epoch_ns", {"values": [text]}) == [expected]


def test_epoch_ns_reads_dbt_datetimes_and_round_trips_to_iso():
    out = run_macro("it_run_started_at", {})
    assert out["ns"] == ns(out["iso"])
    assert out["iso"].endswith("Z") and len(out["iso"]) == len("2026-10-31T17:00:00.123456Z")
    assert out["iso_of_naive"] == "2026-10-31T17:00:00.123456Z"


def test_node_spans_carry_dbt_timings():
    records = run_macro("it_end_run_render", {"results": [A, B]})["records"]
    spans = [r for r in records if r.get("name") == "icecore.node"]
    assert [s["attrs"]["dbt.node.unique_id"] for s in spans] == ["model.p.a", "model.p.b"]
    for span, res in zip(spans, [A, B]):
        compile_, execute = res["timing"]
        assert span["start_ns"] == ns(compile_["started_at"]) and span["end_ns"] == ns(execute["completed_at"])
        a = span["attrs"]
        assert a["dbt.node.started_at"] == compile_["started_at"]
        assert a["dbt.node.completed_at"] == execute["completed_at"]
        assert a["dbt.timing.compile.started_at"] == compile_["started_at"]
        assert a["dbt.timing.compile.completed_at"] == compile_["completed_at"]
        assert a["dbt.timing.execute.started_at"] == execute["started_at"]
        assert a["dbt.timing.execute.completed_at"] == execute["completed_at"]
        assert a["dbt.node.duration_s"] == pytest.approx((span["end_ns"] - span["start_ns"]) / 1e9)
        assert a["dbt.node.type"] == "model" and a["dbt.invocation_id"]
        assert span["events"] == []


def test_nodes_without_timing_and_hook_operations_get_no_span():
    records = run_macro("it_end_run_render", {"results": [A, SKIPPED, HOOK]})["records"]
    assert [r["attrs"]["dbt.node.unique_id"] for r in records if r.get("name") == "icecore.node"] == ["model.p.a"]


def test_invocation_span_runs_from_run_started_at_to_end_run():
    out = run_macro("it_end_run_render", {"results": [A, B]})
    records = out["records"]
    invocation = records[0]
    assert invocation["name"] == "icecore.invocation"
    a = invocation["attrs"]
    assert invocation["start_ns"] == ns(a["dbt.invocation.started_at"]) == out["run_started_at_ns"]
    assert invocation["end_ns"] == ns(a["dbt.invocation.completed_at"]) >= invocation["start_ns"]
    assert a["dbt.invocation.duration_s"] == pytest.approx((invocation["end_ns"] - invocation["start_ns"]) / 1e9)
    assert a["dbt.version"] and a["icecore.anomaly_engine"] == "auto"
    end = records[-1]
    assert end["type"] == "log" and end["attrs"]["icecore.kind"] == "run_end"
    for key in ("dbt.invocation.started_at", "dbt.invocation.completed_at", "dbt.invocation.duration_s"):
        assert end["attrs"][key] == a[key]
    assert [r.get("name", r.get("type")) for r in records] == ["icecore.invocation", "icecore.node", "icecore.node", "log"]


def test_start_record_carries_run_started_at():
    out = run_macro("it_run_start_timing", {})
    assert out["record"]["attrs"]["dbt.invocation.started_at"] == out["run_started_at"]


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture(scope="module")
def build(sf, obs):
    """One `dbt build --select hooks_a+` (three models, one test) and its records."""
    run_id = uuid.uuid4().hex
    since = snowflake_since(sf)
    try:
        res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, obs)
        assert res.success, res.exception
        nodes = node_results(run_results)
        rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=len(nodes) + 2,
                                  since=since)
    finally:
        drop_fixture_views(sf, run_id)
    return {"run_results": run_results, "nodes": nodes, "rows": rows}


def spans(rows: list[dict], name: str) -> list[dict]:
    return [r for r in rows if r["record_type"] == "SPAN" and r["record"].get("name") == name]


@pytest.mark.sandbox
def test_each_node_span_has_the_run_results_timing(build):
    by_id = {s["record_attributes"]["dbt.node.unique_id"]: s for s in spans(build["rows"], "icecore.node")}
    assert sorted(by_id) == sorted(r["unique_id"] for r in build["nodes"])
    assert sum(1 for u in by_id if u.startswith("model.")) == 3
    for res in build["nodes"]:
        span = by_id[res["unique_id"]]
        a = span["record_attributes"]
        started = min(t["started_at"] for t in res["timing"])
        completed = max(t["completed_at"] for t in res["timing"])
        assert ns(a["dbt.node.started_at"]) == ns(started) and ns(a["dbt.node.completed_at"]) == ns(completed)
        for t in res["timing"]:
            assert ns(a[f"dbt.timing.{t['name']}.started_at"]) == ns(t["started_at"])
            assert ns(a[f"dbt.timing.{t['name']}.completed_at"]) == ns(t["completed_at"])
        # The span's own columns hold dbt's times, to the millisecond (S1).
        assert ms_of_ntz(span["start_timestamp"]) == ns(started) // 10**6
        assert ms_of_ntz(span["timestamp"]) == ns(completed) // 10**6
        assert span["scope"]["name"] == "icecore"


@pytest.mark.sandbox
def test_timings_do_not_come_from_ingestion(build):
    # Spans are written by end_run's CALL, after every node completed; their times are still dbt's. Whatever
    # OBSERVED_TIMESTAMP holds (NULL on ICECORE_EMIT records, S3), nothing reads it.
    node_spans = spans(build["rows"], "icecore.node")
    for span in node_spans:
        res = next(r for r in build["nodes"] if r["unique_id"] == span["record_attributes"]["dbt.node.unique_id"])
        assert ms_of_ntz(span["timestamp"]) == ns(max(t["completed_at"] for t in res["timing"])) // 10**6
        observed = span["observed_timestamp"]
        assert observed is None or observed != span["timestamp"]
    # The CALL's own query ran after the run: it is a different, later query than any node's.
    call_ids = {s["resource_attributes"]["snow.query.id"] for s in node_spans}
    node_ids = {r["adapter_response"].get("query_id") for r in build["nodes"]}
    assert call_ids and not (call_ids & node_ids)


@pytest.mark.sandbox
def test_invocation_duration_is_its_end_minus_its_start(build):
    invocation, = spans(build["rows"], "icecore.invocation")
    a = invocation["record_attributes"]
    started, completed = ns(a["dbt.invocation.started_at"]), ns(a["dbt.invocation.completed_at"])
    assert a["dbt.invocation.duration_s"] == pytest.approx((completed - started) / 1e9, abs=1e-6)
    assert ms_of_ntz(invocation["start_timestamp"]) == started // 10**6
    assert ms_of_ntz(invocation["timestamp"]) == completed // 10**6
    # The run holds every node.
    assert started <= min(ns(t["started_at"]) for r in build["nodes"] for t in r["timing"])
    assert completed >= max(ns(t["completed_at"]) for r in build["nodes"] for t in r["timing"])
    end, = run_end_logs(build["rows"])
    assert end["record_attributes"]["dbt.invocation.duration_s"] == a["dbt.invocation.duration_s"]
