"""UC5 follow-up to the cost estimate: it counts every row a run writes (macros/telemetry/end_run.sql,
icecore._icecore_end_run_row_counts; macros/setup/estimate_telemetry_cost.sql).

end_run writes the invocation span (1), one span per node (n), one node-result LOG per node (n, only when var
node_results is on), one `icecore.query` span event per query id (n x query events per node, only when var
query_events is on), the invocation-end LOG (1), plus what the emitters of `_icecore_end_run_emitters()` add. The
estimate reads those counts from the macro that sits next to that list, and a test fails when an emitter is added
to the list without a count there.
"""
import json

import pytest

from helpers.macros import run_macro, run_macro_with_logs

COUNTED_BY_THE_ESTIMATE = {"node_results", "query_events", "lineage", "samples"}


def with_vars(settings: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": settings})]


def result(i: int) -> dict:
    uid = f"model.p.m{i}"
    return {"unique_id": uid, "status": "success", "execution_time": 1.0,
            "timing": [{"name": "execute", "started_at": f"2026-10-31T17:00:0{i}.000000Z",
                        "completed_at": f"2026-10-31T17:00:0{i}.500000Z"}],
            "adapter_response": {"query_id": f"01q-{i}", "rows_affected": 1},
            "node": {"unique_id": uid, "name": f"m{i}", "resource_type": "model", "config": {"materialized": "view"}}}


def estimate(settings: dict | None = None, **kwargs):
    return run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": kwargs},
                               with_vars(settings) if settings is not None else None)[0]


def emitters() -> list[dict]:
    return run_macro("it_end_run_emitters", {})


def test_every_end_run_emitter_has_a_row_count():
    counts = run_macro("it_end_run_row_counts", {"nodes": 7})
    names = {e["name"] for e in emitters()}
    assert not names - set(counts["emitters"]), (
        f"emitters without a row count in _icecore_end_run_row_counts: {sorted(names - set(counts['emitters']))}")
    assert counts["uncounted"] == []


def test_counts_for_n_nodes():
    counts = run_macro("it_end_run_row_counts", {"nodes": 7, "query_events_per_node": 2})
    assert counts["fixed"] == {"invocation_span": 1, "node_spans": 7, "end": 1}
    assert counts["emitters"]["node_results"] == 7 and counts["emitters"]["query_events"] == 14


def test_the_estimate_counts_the_invocation_span_and_node_results():
    out = estimate({"lineage": False}, nodes=100, runs_per_day=24)
    r = out["records_per_run"]
    assert (r["invocation_span"], r["node_spans"], r["node_results"], r["query_events"], r["end"]) == (1, 100, 100, 100, 1)


@pytest.mark.parametrize("settings,expected", [
    ({}, (100, 100)),
    ({"node_results": False}, (0, 100)),
    ({"query_events": False}, (100, 0)),
    ({"node_results": "false", "query_events": False}, (0, 0)),
])
def test_node_result_and_query_event_rows_follow_their_vars(settings, expected):
    r = estimate(settings, nodes=100, runs_per_day=1)["records_per_run"]
    assert (r["node_results"], r["query_events"]) == expected


def test_samples_count_follows_its_var_and_the_failing_tests():
    off = run_macro("it_end_run_row_counts", {"nodes": 7, "failing_tests": 4})
    assert off["emitters"]["samples"] == 0  # opt-in
    on = run_macro("it_end_run_row_counts", {"nodes": 7, "failing_tests": 4}, with_vars({"samples": True}))
    assert on["emitters"]["samples"] == 4


def test_lineage_count_follows_its_var_and_the_payload_cap():
    assert run_macro("it_end_run_row_counts", {"nodes": 7})["emitters"]["lineage"] == 1  # the edges fit one record
    off = run_macro("it_end_run_row_counts", {"nodes": 7}, with_vars({"lineage": False}))
    assert off["emitters"]["lineage"] == 0
    small = run_macro("it_end_run_row_counts", {"nodes": 7}, with_vars({"payload_cap_kb": 1}))
    assert small["emitters"]["lineage"] > 1  # a 1 KB cap splits the edges


@pytest.mark.parametrize("settings", [{}, {"node_results": False}, {"query_events": False}, {"lineage": False},
                                      {"samples": True}, {"node_results": False, "query_events": False, "lineage": False}])
def test_the_estimate_matches_the_rows_end_run_writes(settings):
    n = 3
    # samples on with no failing test: the results below have none, so the estimate counts 0 failing tests
    written = run_macro("it_end_run_rows_written", {"results": [result(i) for i in range(n)]}, with_vars(settings))
    r = estimate(settings, nodes=n, runs_per_day=1, failing_tests_per_run=0)["records_per_run"]
    estimated = (r["invocation_span"] + r["node_spans"] + r["node_results"] + r["query_events"] + r["end"]
                 + r["lineage"] + r["samples"])
    assert estimated == written["rows"], (r, written)
