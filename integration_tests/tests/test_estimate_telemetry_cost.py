"""UC5 telemetry cost estimate: icecore.estimate_telemetry_cost (macros/setup/estimate_telemetry_cost.sql).

Records per run come from the write-path design (spec section 4; spikes/GATE1.md, "Consequences for the build"):
one start record, one end LOG, one span per node, query events per node, one lineage record per 64 KB of edges and
one sample per failing test. Measured per-CALL rows are added: one procedure SPAN per CALL (spikes/S3.md, finding 4)
and about 3 Snowpark LOG rows per ICECORE_EMIT_RUN CALL (spikes/S1.md, "Python noise"). S3 measured lag, not volume.
Credits use Snowflake's Service Consumption Table (effective October 29, 2026), Table 5: Telemetry Data Ingest,
0.0212 Platform Credits per GB. The estimate reads only the dbt graph, so it runs offline.
"""
import json
import os

import pytest

from helpers.macros import IT_DIR, call_macro, invoke, run_macro, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
NODE_TYPES = {"model", "seed", "snapshot", "test"}


def estimate(**kwargs):
    # the fixtures of this project hold icecore monitor tests: the monitor records have their own tests below
    return run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": {"monitors": False, **kwargs}})


def message(logs) -> str:
    return "\n".join(line for line in logs if line.startswith("icecore: telemetry cost"))


# --- offline: the estimate reads only the graph -----------------------------------------------------

def test_cost_true_raises_the_total_by_its_rows_and_its_live_calls():
    base, _ = estimate(nodes=100, runs_per_day=24)
    out, logs = run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": {"nodes": 100, "runs_per_day": 24, "monitors": False}},
                                    ["--vars", '{"icecore": {"cost": true}}'])
    # 100 nodes x (cost LOG + metric event + the backfill's span) = 300 more end_run records: 609 x 1,500 B needs a
    # second ICECORE_EMIT_RUN CALL (+4 rows), plus one live ICECORE_EMIT CALL for the 100 metric events (+1 SPAN)
    assert out["records_per_run"]["cost"] == 300
    assert out["emit_run_calls"] == 2 and out["records_per_run"]["call_overhead"] == 1 + 2 * 4 + 1
    assert out["rows_per_run"] == base["rows_per_run"] + 300 + 5 == 614
    assert out["rows_per_month"] > base["rows_per_month"]
    assert "300 cost" in message(logs)


def test_100_nodes_24_runs_prints_a_monthly_record_count_and_a_credit_range():
    out, logs = estimate(nodes=100, runs_per_day=24)
    # 1 start + 1 invocation span + 1 end + 100 node spans + 100 node results + 100 query events + 1 lineage = 304
    # icecore records, + 1 SPAN for the start CALL + (1 SPAN + 3 Snowpark LOG rows) for the one ICECORE_EMIT_RUN
    # CALL = 309 rows
    assert out["records_per_run"] == {"start": 1, "invocation_span": 1, "end": 1, "node_spans": 100,
                                      "node_results": 100, "query_events": 100, "lineage": 1, "samples": 0,
                                      "root_cause": 0, "cost": 0, "call_overhead": 5}
    assert out["rows_per_run"] == 309
    assert out["rows_per_month"] == 309 * 24 * 30 == 222_480
    low, high = out["credits_per_month"]
    assert low == pytest.approx(222_480 * 1500 / 1e9 * 0.0212)
    assert high == pytest.approx(222_480 * 64 * 1024 / 1e9 * 0.0212)
    text = message(logs)
    assert "222,480 rows per month" in text
    assert f"{low:.4f} to {high:.4f} credits per month" in text


def test_missing_runs_per_day_is_asked_for_and_does_not_fail():
    out, logs = estimate(nodes=100)
    assert out["status"] == "needs_runs_per_day"
    assert out["rows_per_run"] == 309 and out["rows_per_month"] is None and out["credits_per_month"] is None
    assert "runs_per_day" in message(logs) and "--args '{runs_per_day: <n>}'" in message(logs)


@pytest.mark.parametrize("runs_per_day", [0, -3, "often"])
def test_unusable_runs_per_day_is_asked_for(runs_per_day):
    out, _ = estimate(nodes=10, runs_per_day=runs_per_day)
    assert out["status"] == "needs_runs_per_day"


def test_the_estimate_states_its_sources():
    _, logs = estimate(nodes=100, runs_per_day=24)
    text = message(logs)
    assert "write-path design" in text and "spikes/GATE1.md" in text
    assert "spikes/S3.md" in text and "lag, not volume" in text
    assert "spikes/S1.md" in text
    assert "Service Consumption Table" in text and "0.0212 Platform Credits per GB" in text
    assert "LOGGING" in text and "TELEMETRY_DATA_INGEST" in text


def test_samples_and_query_events_follow_their_arguments():
    # samples is opt-in: counted only with the var on; lineage off by its var
    vars_ = ["--vars", json.dumps({"icecore": {"samples": True, "lineage": False}})]
    out, _ = run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": {
        "nodes": 10, "runs_per_day": 1, "query_events_per_node": 3, "failing_tests_per_run": 2, "monitors": False}}, vars_)
    r = out["records_per_run"]
    assert (r["query_events"], r["samples"], r["lineage"]) == (30, 2, 0)
    # default vars: samples are off, so failing tests add no rows
    out, _ = estimate(nodes=10, runs_per_day=1, failing_tests_per_run=2)
    assert out["records_per_run"]["samples"] == 0


def test_large_runs_need_more_emit_run_calls():
    out, _ = estimate(nodes=1000, runs_per_day=1)
    # 1 + 1 + 1000 + 1000 + 1000 + 1 lineage = 3003 end-run records at 1500 bytes = about 4.5 MB: 9 CALLs of 512 KB
    assert out["emit_run_calls"] == 9
    assert out["records_per_run"]["call_overhead"] == 1 + 9 * 4


def test_nodes_default_to_the_graph():
    assert invoke(["parse"]).success
    manifest = json.loads((IT_DIR / "target" / "manifest.json").read_text())
    expected = sum(1 for n in manifest["nodes"].values() if n["resource_type"] in NODE_TYPES)
    out, _ = estimate(runs_per_day=1)
    assert out["nodes"] == expected > 0


def test_setup_runs_the_estimate_after_recording_capabilities():
    names = [s["name"] for s in call_macro("_icecore_setup_steps") if s["ready"]]
    assert names.index("record_capabilities") + 1 == names.index("estimate_telemetry_cost")


# --- offline: monitor records (the icecore generic tests) ---------------------------------------------

def monitor_test(kind, attached="source.p.s.t", **kwargs):
    return {"unique_id": f"test.p.{kind}_{abs(hash((kind, str(kwargs))))}", "resource_type": "test", "attached_node": attached,
            "test_metadata": {"name": kind, "namespace": "icecore", "kwargs": kwargs}}


def monitor_records(nodes, sources=None, **kwargs):
    return run_macro("it_call", {"name": "_icecore_monitor_records",
                                 "kwargs": {"nodes": nodes, "sources": sources or {}, **kwargs}})


def test_a_project_without_monitor_tests_has_no_monitor_entry_and_an_unchanged_estimate():
    nodes = [{"unique_id": "test.p.not_null_x", "resource_type": "test", "attached_node": "model.p.m",
              "test_metadata": {"name": "not_null", "namespace": None, "kwargs": {"column_name": "id"}}},
             # a project's own test that happens to be called freshness is not icecore's
             {"unique_id": "test.p.freshness_own", "resource_type": "test",
              "test_metadata": {"name": "freshness", "namespace": None, "kwargs": {}}}]
    assert monitor_records(nodes) == {"tests": 0, "metrics": 0, "anomalies": 0, "schema_signatures": 0, "records": 0, "calls": 0,
                                      "dimension_tests": 0, "dimension_values": 0, "assumed_column_tests": 0}
    # with the monitor tests of the graph left out the estimate is the one of a project without them
    out, logs = estimate(nodes=100, runs_per_day=24)
    assert "monitors" not in out["records_per_run"] and out["rows_per_run"] == 309
    assert "monitor records" not in message(logs)


def test_one_metric_record_per_computed_metric_and_one_signature_per_schema_changes_test():
    sources = {"source.p.s.t": {"columns": {"a": {}, "b": {}, "c": {}}}}
    nodes = [monitor_test("freshness", max_lag="2h"), monitor_test("volume", min_rows=1), monitor_test("schema_changes"),
             monitor_test("column_anomaly", metrics=["null_rate", "avg", "max"], column_name="a"),
             monitor_test("column_anomaly", column_name="a"),                          # default metrics: window_rows, null_rate
             monitor_test("all_columns_anomaly", exclude_columns=["c"]),                # 2 default metrics x (3 - 1) columns
             monitor_test("dimension_anomaly", max_values=5, column_name="b")]          # 5 values + other
    out = monitor_records(nodes, sources)
    # metrics: 1 + 1 + 3 + 2 + 4 + 6 = 17; anomaly warn upper bound: one per metric = 17; one signature
    assert (out["tests"], out["metrics"], out["anomalies"], out["schema_signatures"]) == (7, 17, 17, 1)
    assert out["records"] == 35 and out["dimension_values"] == 6
    # one ICECORE_EMIT CALL per test, 100 events each: the 7 tests send 1, 1, 1, 6, 4, 8, 12 events
    assert out["calls"] == 7


def test_untyped_columns_and_wide_tables_follow_their_assumptions():
    # no declared columns: assumed_columns (default 10) for all_columns_anomaly
    assert monitor_records([monitor_test("all_columns_anomaly")])["metrics"] == 20
    assert monitor_records([monitor_test("all_columns_anomaly")], assumed_columns=3)["metrics"] == 6
    assert monitor_records([monitor_test("all_columns_anomaly")])["assumed_column_tests"] == 1
    # dimension_anomaly with no max_values reads the var (20) plus the other group
    assert monitor_records([monitor_test("dimension_anomaly", column_name="b")])["metrics"] == 21
    # a signature over the payload cap (64 KB) is split: 1,400 declared columns at about 100 bytes = 140 KB = 3 records
    wide = {"source.p.s.t": {"columns": {f"c{i}": {} for i in range(1400)}}}
    assert monitor_records([monitor_test("schema_changes")], wide)["schema_signatures"] == 3
    # more than 100 events in a test need more than one CALL
    assert monitor_records([monitor_test("dimension_anomaly", max_values=60, column_name="b")])["calls"] == 2


def test_the_estimate_adds_the_monitor_line_and_keeps_the_others():
    base, base_logs = estimate(runs_per_day=24)
    out, logs = run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": {"runs_per_day": 24}})
    mon = out["records_per_run"]["monitors"]
    # the fixtures hold monitor tests: their records are their own entry, and so are their procedure spans
    assert mon > 0 and out["rows_per_run"] > base["rows_per_run"]
    assert out["records_per_run"]["call_overhead"] > base["records_per_run"]["call_overhead"]
    assert out["rows_per_run"] == base["rows_per_run"] + mon + (out["records_per_run"]["call_overhead"] - base["records_per_run"]["call_overhead"])
    text = message(logs)
    assert f"{mon:,} are icecore monitor records" in text
    assert "anomaly warn events (an upper bound" in text and "schema signatures" in text and "silence verdicts" in text
    # the other entries and the text of the existing lines do not change
    assert {k: v for k, v in out["records_per_run"].items() if k not in ("monitors", "call_overhead")} == \
           {k: v for k, v in base["records_per_run"].items() if k != "call_overhead"}
    assert "credits per month" in text and "monitor records" not in message(base_logs)


# --- sandbox --------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_setup_prints_the_estimate(sf, run_id):
    schema = f"ICECORE_OBS_{run_id}".upper()
    try:
        out, logs = run_macro_with_logs("it_call", {"name": "setup", "kwargs": {"schema": schema, "runs_per_day": 24}})
        step = next(s for s in out["steps"] if s["name"] == "estimate_telemetry_cost")
        assert step["status"] == "done" and "rows per month" in step["detail"], step
        assert "credits per month" in message(logs)
    finally:
        sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
