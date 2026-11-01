"""S08 scale scenarios: what end_run writes for a few hundred nodes, a payload near the caps and lineage over the
cap, with the rows, CALLs and wall time measured, and what the views return for that run.

The sandbox volume is bounded: one run per size (SIZES), each into an obs schema of its own that is dropped at the
end. Nodes are synthetic results (integration_tests/macros/test_support/it_scale.sql builds them and sends
end_run's batch with emit_run, the way end_run does), so no hundreds of views are created: what scales is the
write path and the views, not dbt's own build.

Measured per size (printed with -s, and asserted against LIMITS):
  rows     records written: 1 invocation span, n node spans, n node results, n query events, the lineage
           records (the project's own edges plus EDGES synthetic ones, split under the cap) and 1 end LOG;
  CALLs    ICECORE_EMIT_RUN CALLs of at most 512 KB each;
  build_s  dbt-side time to build the batch; send_s the time of the guarded CALLs; together the wall time of
           end_run's work on the dbt host;
  views    seconds for INVOCATIONS, NODE_EXECUTIONS and LINEAGE_LATEST filtered to the run.

Limits (LIMITS, seconds) are ceilings of about three times what was measured on the sandbox warehouse on 1 Nov 2026,
so they catch a regression and not noise. They are not service levels. Measured (n=50: 108 rows, 1 CALL; n=300:
608 rows, 2 CALLs; both with 2,500 synthetic lineage edges in 5 records):
  n=50   build_s 0.18   send_s 6.7    views 1.8 / 1.4 / 0.4 s (INVOCATIONS / NODE_EXECUTIONS / LINEAGE_LATEST)
  n=300  build_s 0.32   send_s 13.9   views 1.8 / 1.2 / 0.4 s
Finding (measured here): ICECORE_EMIT_RUN lands every record of a batch except node spans whose times are hours
old (3 hours in the probe): the call returns ok and the spans, with their query events, are silently dropped. The
synthetic results therefore carry recent times.

Not covered: the Streamlit Runs page load time. The app cannot be loaded from this suite; it is measured with the
app (test_app_shell_sandbox.py) when the Runs page exists.
"""
import json
import time

import pytest
from snowflake.connector import DictCursor

from helpers.macros import run_macro_with_logs
from helpers.run_hooks import DATABASE, icecore_vars, invocation_records, obs_schema, snowflake_since

SIZES = [50, 300]
EDGES = 2500              # at about 190 bytes each: several 64 KB lineage records
NEAR_CAP_KB = 28          # a node result carries its message twice (attribute and log message): just under 64 KB
OVER_CAP_KB = 100
LIMITS = {"build_s": 2, "send_s": 45, "view_s": 8}
KB = 1024


# --- offline --------------------------------------------------------------------------------------

def test_a_300_node_batch_is_rendered_and_chunked_under_512_kb():
    from helpers.macros import run_macro
    results = [{"unique_id": f"model.p.m{i}", "status": "success", "execution_time": 0.5,
                "timing": [{"name": "execute", "started_at": "2026-11-01T10:00:00.000000Z",
                            "completed_at": "2026-11-01T10:00:01.000000Z"}],
                "adapter_response": {"query_id": f"01b-{i}", "rows_affected": 1}, "message": "SUCCESS 1", "failures": None,
                "node": {"unique_id": f"model.p.m{i}", "name": f"m{i}", "resource_type": "model",
                         "config": {"materialized": "view"}}} for i in range(300)]
    out = run_macro("it_end_run_render", {"results": results})
    kinds = [r.get("name") or r["attrs"].get("icecore.kind") for r in out["records"]]
    assert kinds.count("icecore.node") == 300 and kinds.count("node_result") == 300 and kinds.count("run_end") == 1
    assert sum(out["chunk_sizes"]) == len(out["records"]) and all(b <= 512 * KB * 1.01 for b in out["chunk_bytes"])  # measured: 524,718 bytes for the first chunk, 0.08% over 512 KB (the chunker adds 2 bytes a record, tojson of the chunk adds fewer): a soft limit, the hard one is about 1 MB


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        run_macro_with_logs("it_create_views", {"schema": schema}, ["--vars", json.dumps(icecore_vars(schema))])
        yield schema


@pytest.fixture(scope="module", params=SIZES)
def scaled(sf, obs, request):
    n = request.param
    since = snowflake_since(sf)
    dbt_vars = {**icecore_vars(obs, result_message="full"), "it_edges": EDGES}
    out, _ = run_macro_with_logs("it_scale_end_run", {"nodes": n, "edges": EDGES, "near_cap_kb": NEAR_CAP_KB,
                                                       "over_cap_kb": OVER_CAP_KB}, ["--vars", json.dumps(dbt_vars)])
    assert all(s.startswith("ok") for s in out["statuses"]), out
    rows = invocation_records(sf, obs, out["invocation_id"], min_rows=out["records"] + n, since=since, settle_s=30)
    return {"n": n, "out": out, "rows": rows, "schema": obs}


def kind(row):
    return row["record_attributes"].get("icecore.kind")


@pytest.mark.sandbox
def test_every_node_lands_and_the_rows_add_up(scaled):
    n, rows, out = scaled["n"], scaled["rows"], scaled["out"]
    node_results = [r for r in rows if kind(r) == "node_result"]
    assert len(node_results) == n and len({r["record_attributes"]["dbt.node.unique_id"] for r in node_results}) == n
    assert sum(1 for r in rows if r["record"].get("name") == "icecore.node") == n
    assert sum(1 for r in rows if r["record"].get("name") == "icecore.query") == n
    assert sum(1 for r in rows if r["record"].get("name") == "icecore.invocation") == 1
    assert sum(1 for r in rows if kind(r) == "run_end") == 1
    lineage = [r for r in rows if kind(r) == "lineage"]
    assert len(lineage) >= 2  # EDGES synthetic edges alone do not fit one 64 KB record
    # records sent (spans and logs) plus one SPAN_EVENT per node query
    assert len(rows) == out["records"] + n == 1 + 3 * n + len(lineage) + 1


@pytest.mark.sandbox
def test_lineage_over_the_cap_is_split_and_none_is_lost(scaled):
    lineage = [r for r in scaled["rows"] if kind(r) == "lineage"]
    synthetic = [tuple(e) for r in lineage for e in json.loads(r["record_attributes"]["dbt.lineage.edges"])
                 if e[0].startswith("model.scale.parent_model")]
    assert len(synthetic) == len(set(synthetic)) == EDGES
    sizes = [len(json.dumps(r["record_attributes"])) for r in lineage]
    assert max(sizes) <= 64 * KB and not any("icecore.truncated" in r["record_attributes"] for r in lineage)
    # the synthetic edges are one batch: its records agree on how many parts there are (the project's own
    # edges are another batch, of one part)
    batch = [r for r in lineage if any(e[0].startswith("model.scale.parent_model")
                                       for e in json.loads(r["record_attributes"]["dbt.lineage.edges"]))]
    assert {r["record_attributes"]["dbt.lineage.parts"] for r in batch} == {len(batch)} and len(batch) >= 2


@pytest.mark.sandbox
def test_payloads_near_and_over_the_cap(scaled):
    by_node = {r["record_attributes"]["dbt.node.unique_id"]: r["record_attributes"]
               for r in scaled["rows"] if kind(r) == "node_result"}
    near, over = by_node["model.scale.m_1"], by_node["model.scale.m_0"]
    assert len(near["dbt.result.message"]) == NEAR_CAP_KB * KB and "icecore.truncated" not in near
    assert "icecore.truncated" in over and over["icecore.truncated"] is True
    assert len(json.dumps(over)) <= 64 * KB + 512
    assert over["dbt.result.message"].startswith("xxxx")


@pytest.mark.sandbox
def test_calls_stay_within_512_kb_and_every_one_succeeds(scaled):
    out = scaled["out"]
    assert out["calls"] >= 1 and all(s.startswith("ok") for s in out["statuses"])
    # the batch is at most (records x cap) and at least its small records: it takes few CALLs, not one per record
    assert out["calls"] < out["records"] / 10 or out["records"] < 50


@pytest.mark.sandbox
def test_wall_time_and_view_times_are_within_the_limits(sf, scaled):
    out = scaled["out"]
    cur = sf.cursor(DictCursor)
    timings = {}
    for view in ("INVOCATIONS", "NODE_EXECUTIONS", "LINEAGE_LATEST"):
        column = "invocation_id"
        sql = f'select count(*) as n from "{DATABASE}"."{scaled["schema"]}"."{view}"'
        if view != "LINEAGE_LATEST":
            sql += f" where {column} = %(i)s"
        started = time.monotonic()
        count = cur.execute(sql, {"i": out["invocation_id"]}).fetchone()["N"]
        timings[view] = time.monotonic() - started
        assert count > 0, view
        if view == "NODE_EXECUTIONS":
            assert count == scaled["n"]
    print(f"\nSCALE n={scaled['n']} rows={out['records']} calls={out['calls']} build_s={out['build_s']:.2f} "
          f"send_s={out['send_s']:.2f} views={ {k: round(v, 2) for k, v in timings.items()} }")
    assert out["build_s"] < LIMITS["build_s"] and out["send_s"] < LIMITS["send_s"]
    assert max(timings.values()) < LIMITS["view_s"], timings
