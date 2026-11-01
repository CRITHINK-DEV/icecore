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

from helpers.macros import IT_DIR, call_macro, invoke, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
NODE_TYPES = {"model", "seed", "snapshot", "test"}


def estimate(**kwargs):
    return run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": kwargs})


def message(logs) -> str:
    return "\n".join(line for line in logs if line.startswith("icecore: telemetry cost"))


# --- offline: the estimate reads only the graph -----------------------------------------------------

def test_cost_true_raises_the_total_by_its_rows_and_its_live_calls():
    base, _ = estimate(nodes=100, runs_per_day=24)
    out, logs = run_macro_with_logs("it_call", {"name": "estimate_telemetry_cost", "kwargs": {"nodes": 100, "runs_per_day": 24}},
                                    ["--vars", '{"icecore": {"cost": true}}'])
    # 100 nodes x (cost LOG + metric event) = 200 more end_run records: 509 x 1,500 B needs a second ICECORE_EMIT_RUN
    # CALL (+4 rows), plus one live ICECORE_EMIT CALL for the 100 metric events (+1 SPAN)
    assert out["records_per_run"]["cost"] == 200
    assert out["emit_run_calls"] == 2 and out["records_per_run"]["call_overhead"] == 1 + 2 * 4 + 1
    assert out["rows_per_run"] == base["rows_per_run"] + 200 + 5 == 514
    assert out["rows_per_month"] > base["rows_per_month"]
    assert "200 cost" in message(logs)


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
        "nodes": 10, "runs_per_day": 1, "query_events_per_node": 3, "failing_tests_per_run": 2}}, vars_)
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
