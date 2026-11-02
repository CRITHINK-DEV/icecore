"""UC4 attach_cost, the end-of-run emitter `cost` (macros/cost/attach_cost.sql, var icecore.cost, default false).

Offline: the emitter entry, the cost LOG and metric shapes, the all-or-nothing sum per node, the lookup SQL, and
that var cost false adds no cost record to end_run's batch. Sandbox: the lookup against
SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY with query ids from previous days (the view lags by hours, so a
fresh run is never attributed at on-run-end), the records that land, and the warning when nothing is attributed.
"""
import json
import time
import uuid

import pytest
import yaml

from helpers.macros import IT_DIR, run_macro, run_macro_with_logs
from helpers.run_hooks import (
    drop_fixture_views, invocation_records, node_results, obs_schema, run_with_hooks, snowflake_since,
)

NODE = {"dbt.node.unique_id": "model.p.a", "dbt.node.type": "model", "dbt.node.name": "a",
        "dbt.node.relation": '"DB"."S"."A"'}


# --- offline ---------------------------------------------------------------------------------------

def test_cost_is_one_emitter_behind_var_cost_default_false():
    emitters = run_macro("it_end_run_emitters", {})
    assert [e for e in emitters if e["name"] == "cost"] == [{"name": "cost", "var": "cost", "default": False}]
    assert yaml.safe_load(open(IT_DIR.parent / "dbt_project.yml"))["vars"]["icecore"]["cost"] is False


def test_var_cost_false_adds_no_cost_record():
    result = {"unique_id": "model.p.a", "status": "success", "timing": [], "adapter_response": {"query_id": "01q-a"},
              "node": {"unique_id": "model.p.a", "name": "a", "resource_type": "model", "config": {}}}
    records = run_macro("it_end_run_render", {"results": [result]})["records"]
    assert not [r for r in records if r.get("attrs", {}).get("icecore.kind") == "cost"
                or any(k.startswith("dbt.cost.") for k in r.get("attrs", {}))]


def test_node_cost_sums_its_queries_and_names_warehouses():
    costs = {"q1": {"credits": 0.25, "warehouse": "WH_B"}, "q2": {"credits": 0.5, "warehouse": "WH_A"},
             "q3": {"credits": 1.0, "warehouse": "WH_A"}}
    out = run_macro("it_node_costs", {"ids_by_node": {"n.one": ["q1", "q2"], "n.two": ["q3"]}, "costs": costs})
    assert out == {"n.one": {"credits": 0.75, "warehouse": "WH_A,WH_B"}, "n.two": {"credits": 1.0, "warehouse": "WH_A"}}


def test_node_with_one_unattributed_query_gets_no_cost_yet():
    costs = {"q1": {"credits": 0.25, "warehouse": "WH"}}
    out = run_macro("it_node_costs", {"ids_by_node": {"n.one": ["q1", "q9"], "n.two": ["q9"], "n.three": ["q1"]},
                                      "costs": costs})
    assert list(out) == ["n.three"]


def test_settled_node_closes_at_what_was_attributed_and_says_why():
    costs = {"q1": {"credits": 0.25, "warehouse": "WH"}}
    out = run_macro("it_node_costs", {"ids_by_node": {"n.none": ["q9"], "n.part": ["q1", "q9"], "n.full": ["q1"]},
                                      "costs": costs, "settled": {"n.none": True, "n.part": True, "n.full": True}})
    assert out["n.none"] == {"credits": 0.0, "warehouse": None, "settled": "no_attribution"}
    assert out["n.part"] == {"credits": 0.25, "warehouse": "WH", "settled": "no_attribution"}
    assert "settled" not in out["n.full"]  # every query was attributed: a real cost


def test_settled_cost_log_carries_the_flag_and_has_no_metric():
    cost = {"credits": 0.0, "warehouse": None, "settled": "no_attribution"}
    out = run_macro("it_cost_items", {"node_attrs": NODE, "cost": cost})
    assert out["log"]["attrs"]["dbt.cost.settled"] == "no_attribution" and out["log"]["attrs"]["dbt.cost.credits"] == 0.0
    assert out["metric"] == []


def test_settle_hours_zero_never_settles_when_the_attribution_read_failed():
    zero = ["--vars", json.dumps({"icecore": {"cost_settle_hours": 0}})]
    ids = {"n.a": ["q1"], "n.b": ["q2"]}
    ok = run_macro("it_settled_at_run_end", {"ids_by_node": ids, "found": {"costs": {}, "error": None}}, zero)
    assert ok == {"n.a": True, "n.b": True}
    failed = run_macro("it_settled_at_run_end", {"ids_by_node": ids, "found": {"costs": {}, "error": "no grant"}}, zero)
    assert failed == {}


def test_settle_after_24_hours_by_default_and_the_var_changes_it():
    assert run_macro("it_cost_settled", {"age_hours": 23}) == {"settled": False, "hours": 24}
    assert run_macro("it_cost_settled", {"age_hours": 24})["settled"] is True
    out = run_macro("it_cost_settled", {"age_hours": 0}, ["--vars", json.dumps({"icecore": {"cost_settle_hours": 0}})])
    assert out == {"settled": True, "hours": 0}


def test_cost_log_and_metric_shapes():
    out = run_macro("it_cost_items", {"node_attrs": NODE, "cost": {"credits": 0.021, "warehouse": "TRANSFORM_WH"}})
    log = out["log"]
    assert log["type"] == "log" and log["level"] == "info"
    assert log["attrs"]["icecore.kind"] == "cost"
    assert log["attrs"]["dbt.cost.credits"] == 0.021 and log["attrs"]["dbt.cost.warehouse"] == "TRANSFORM_WH"
    assert log["attrs"]["dbt.node.unique_id"] == "model.p.a"
    metric, = out["metric"]
    assert metric["kind"] == "metric"
    a = metric["attributes"]
    assert a["dbt.metric.name"] == "credits" and a["dbt.metric.value"] == 0.021
    assert a["dbt.node.relation"] == '"DB"."S"."A"' and a["dbt.node.unique_id"] == "model.p.a"


def test_metric_of_a_node_without_relation_is_keyed_by_unique_id():
    node = {"dbt.node.unique_id": "test.p.t", "dbt.node.type": "test"}
    out = run_macro("it_cost_items", {"node_attrs": node, "cost": {"credits": 1, "warehouse": None}})
    assert out["metric"][0]["attributes"]["dbt.node.relation"] == "test.p.t"
    assert "dbt.cost.warehouse" not in out["log"]["attrs"]


def test_lookup_sql_reads_attribution_history_for_the_ids_and_escapes_them():
    sql = run_macro("it_cost_lookup_sql", {"query_ids": ["01a", "x'y"]})
    assert "snowflake.account_usage.query_attribution_history" in sql
    assert "credits_attributed_compute" in sql and "credits_used_query_acceleration" in sql
    assert "query_id in ('01a', 'x\\'y')" in sql


BACKFILL_NODE = {"invocation_id": "inv-old", "unique_id": "model.p.a", "type": "model", "name": "a",
                 "materialization": "view", "relation": '"DB"."S"."A"', "query_ids": ["q1"],
                 "node_at": "2026-10-31T12:00:00.000Z", "age_hours": 5}


ZERO_HOURS = ["--vars", json.dumps({"icecore": {"cost_settle_hours": 0}})]


def _old(inv, uid, *ids):
    return {"invocation_id": inv, "unique_id": uid, "query_ids": list(ids), "age_hours": 48}


def test_forced_read_failure_settles_nothing_and_writes_no_zero_cost():
    pending = [_old("i", "n.a", "q-FAIL-1"), _old("i", "n.b", "q-FAIL-2")]
    for extra in ([], ZERO_HOURS):
        out = run_macro("it_backfill_costs_stubbed", {"pending": pending}, extra)
        assert out["found"]["error"] == "forced read failure"
        assert out["costs"] == {}


def test_partial_chunk_failure_settles_only_nodes_whose_ids_were_all_read():
    # chunk_size 2: chunk 1 = [q-ok-1, q-ok-2] reads, chunk 2 = [q-FAIL-3, q-ok-4] fails, chunk 3 = [q-ok-5] reads
    pending = [_old("i", "n.read", "q-ok-1", "q-ok-2"), _old("i", "n.failed", "q-FAIL-3"),
               _old("i", "n.mixed", "q-ok-4", "q-ok-5")]
    out = run_macro("it_backfill_costs_stubbed", {"pending": pending, "chunk_size": 2})
    assert out["found"]["failed_ids"] == ["q-FAIL-3", "q-ok-4"]
    assert out["costs"] == {"i|n.read": {"credits": 1.0, "warehouse": "WH"}}  # no zero record for failed or mixed nodes
    # a clean read of the same kind of id: a real cost, no failed ids
    clean = run_macro("it_backfill_costs_stubbed", {"pending": [_old("i", "n.ok", "q-ok-1")]})
    assert clean["costs"]["i|n.ok"]["credits"] == 0.5 and clean["found"]["failed_ids"] == []


def test_node_with_a_failed_id_is_not_settled_even_when_marked_settled():
    out = run_macro("it_node_costs", {"ids_by_node": {"n.f": ["q1", "q9"], "n.u": ["q8"]}, "costs": {"q1": {"credits": 0.25, "warehouse": "WH"}},
                                      "settled": {"n.f": True, "n.u": True}, "failed_ids": ["q9"]})
    assert out == {"n.u": {"credits": 0.0, "warehouse": None, "settled": "no_attribution"}}


def test_backfilled_cost_keeps_the_original_invocation_on_every_record():
    out = run_macro("it_backfill_items", {"node": BACKFILL_NODE, "cost": {"credits": 0.5, "warehouse": "WH"},
                                          "backfilled_by": "inv-new", "now_ns": 1762000000000000000})
    assert out["current_invocation_id"] != "inv-old"
    log, span = out["records"]
    assert log["type"] == "log" and log["attrs"]["icecore.kind"] == "cost"
    assert log["attrs"]["dbt.invocation_id"] == "inv-old"
    assert log["attrs"]["dbt.cost.credits"] == 0.5 and log["attrs"]["dbt.cost.warehouse"] == "WH"
    assert log["attrs"]["dbt.node.unique_id"] == "model.p.a" and log["attrs"]["dbt.node.relation"] == '"DB"."S"."A"'
    assert log["attrs"]["icecore.backfill"] is True and log["attrs"]["icecore.backfilled_by"] == "inv-new"
    assert span["name"] == "icecore.cost_backfill" and span["attrs"]["dbt.invocation_id"] == "inv-old"
    metric, = span["events"]
    assert metric["name"] == "icecore.metric" and metric["ts_ns"] == 1762000000000000000
    assert metric["attrs"]["dbt.invocation_id"] == "inv-old"
    assert metric["attrs"]["dbt.metric.name"] == "credits" and metric["attrs"]["dbt.metric.value"] == 0.5
    assert metric["attrs"]["dbt.node.relation"] == '"DB"."S"."A"' and metric["attrs"]["dbt.node.unique_id"] == "model.p.a"


def test_backfilled_items_carry_the_original_node_time():
    out = run_macro("it_backfill_items", {"node": BACKFILL_NODE, "cost": {"credits": 0.5, "warehouse": "WH"},
                                          "backfilled_by": "inv-new", "now_ns": 1762000000000000000})
    log, span = out["records"]
    assert log["attrs"]["dbt.cost.node_at"] == "2026-10-31T12:00:00.000Z"
    assert span["events"][0]["attrs"]["dbt.cost.node_at"] == "2026-10-31T12:00:00.000Z"


def test_settled_backfill_is_a_cost_log_without_metric_span():
    cost = {"credits": 0.0, "warehouse": None, "settled": "no_attribution"}
    out = run_macro("it_backfill_items", {"node": BACKFILL_NODE, "cost": cost, "backfilled_by": "inv-new",
                                          "now_ns": 1762000000000000000})
    log, = out["records"]
    assert log["attrs"]["dbt.cost.settled"] == "no_attribution" and log["attrs"]["dbt.invocation_id"] == "inv-old"


def test_pending_sql_skips_costed_nodes_and_the_current_invocation_and_is_bounded():
    sql = run_macro("it_pending_cost_sql", {"events_sql": "select 1 as x", "days": 7, "limit": 50,
                                            "current_invocation_id": "inv-now"})
    assert "cost_credits is not null" in sql and "c.node_unique_id is null" in sql
    assert "invocation_id <> 'inv-now'" in sql and "dateadd(day, -7, current_timestamp())" in sql
    assert "limit 50" in sql and "array_agg(distinct w.node_query_id)" in sql


ANOMALY = {"engine": "native", "expected": 0.1, "lower": 0.09, "upper": 0.11, "score": 40.0, "is_anomaly": True}


def test_a_regression_adds_an_anomaly_event_after_the_metric():
    metric, anomaly = run_macro("it_cost_metric_scored", {"node_attrs": NODE, "cost": {"credits": 0.5, "warehouse": "WH"},
                                                          "anomaly": ANOMALY})
    assert metric["kind"] == "metric" and anomaly["kind"] == "anomaly" and anomaly["level"] == "warn"
    for e in (metric, anomaly):
        assert e["attributes"]["dbt.anomaly.is_anomaly"] is True and e["attributes"]["dbt.anomaly.score"] == 40.0
        assert e["attributes"]["dbt.node.unique_id"] == "model.p.a" and e["attributes"]["dbt.metric.name"] == "credits"
    assert "credits" in anomaly["message"]


def test_stable_credits_have_no_anomaly_event():
    events = run_macro("it_cost_metric_scored", {"node_attrs": NODE, "cost": {"credits": 0.1, "warehouse": "WH"},
                                                 "anomaly": {**ANOMALY, "is_anomaly": False, "score": 0.1}})
    assert [e["kind"] for e in events] == ["metric"]
    assert events[0]["attributes"]["dbt.anomaly.is_anomaly"] is False


def test_backfilled_regression_is_an_anomaly_span_event_on_the_original_invocation():
    items = run_macro("it_backfill_items_scored", {"node": BACKFILL_NODE, "cost": {"credits": 0.5, "warehouse": "WH"},
                                                   "backfilled_by": "inv-new", "now_ns": 1762000000000000000,
                                                   "anomaly": ANOMALY})
    span = items[1]
    assert [e["name"] for e in span["events"]] == ["icecore.metric", "icecore.anomaly"]
    anomaly = span["events"][1]["attrs"]
    assert anomaly["dbt.invocation_id"] == "inv-old" and anomaly["dbt.anomaly.is_anomaly"] is True
    assert anomaly["icecore.level"] == "warn" and "credits" in anomaly["icecore.message"]


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture(scope="module")
def old_queries(sf):
    """Four attributed queries older than 12 hours: {query_id: (warehouse, credits)}."""
    rows = sf.cursor().execute(
        "select query_id, warehouse_name, credits_attributed_compute + coalesce(credits_used_query_acceleration, 0)"
        " from snowflake.account_usage.query_attribution_history"
        " where start_time between dateadd(day, -6, current_timestamp()) and dateadd(hour, -12, current_timestamp())"
        " and credits_attributed_compute > 0 order by start_time limit 4").fetchall()
    assert len(rows) == 4, "the sandbox has no attributed query from previous days"
    return {r[0]: (r[1], float(r[2])) for r in rows}


def result_for(unique_id, query_id):
    return {"unique_id": unique_id, "status": "success", "timing": [], "adapter_response": {"query_id": query_id},
            "node": {"unique_id": unique_id, "name": unique_id.split(".")[-1], "resource_type": "model",
                     "database": "ICECORE_SANDBOX", "schema": "SPIKES", "config": {"materialized": "view"}}}


def attach(obs, results):
    extra = ["--vars", json.dumps({"icecore": {"obs_schema": obs, "cost": True}})]
    return run_macro_with_logs("it_attach_cost_run", {"results": results}, extra)


@pytest.mark.sandbox
def test_attributed_nodes_get_credits_a_cost_log_and_a_metric(sf, obs, old_queries):
    since = snowflake_since(sf)
    ids = list(old_queries)[:3]
    results = [result_for(f"model.p.n{i}", q) for i, q in enumerate(ids)]
    out, _ = attach(obs, results)
    costs = [i for i in out["items"] if i["attrs"].get("icecore.kind") == "cost"]
    assert sorted(c["attrs"]["dbt.node.unique_id"] for c in costs) == sorted(r["unique_id"] for r in results)
    rows = invocation_records(sf, obs, out["invocation_id"], min_rows=2 * len(results), since=since)
    logs = {r["record_attributes"]["dbt.node.unique_id"]: r["record_attributes"] for r in rows
            if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "cost"}
    metrics = {r["record_attributes"]["dbt.node.unique_id"]: r["record_attributes"] for r in rows
               if r["record_type"] == "SPAN_EVENT" and r["record"].get("name") == "icecore.metric"}
    for i, q in enumerate(ids):
        warehouse, credits = old_queries[q]
        a = logs[f"model.p.n{i}"]
        assert a["dbt.cost.credits"] == pytest.approx(credits) and a["dbt.cost.warehouse"] == warehouse
        m = metrics[f"model.p.n{i}"]
        assert m["dbt.metric.name"] == "credits" and m["dbt.metric.value"] == pytest.approx(credits)


@pytest.mark.sandbox
def test_unattributed_query_logs_a_warning_and_writes_nothing(sf, obs):
    since = snowflake_since(sf)
    out, logs = attach(obs, [result_for("model.p.fresh", "01c00000-0000-0000-0000-000000000000")])
    assert out["items"] == []
    assert any("cost: attribution is not available yet for 1 of 1 nodes" in line for line in logs), logs


@pytest.fixture(scope="module")
def builds(sf, obs):
    """One build with cost on and one with cost off; a fresh run is not attributed yet at on-run-end."""
    out = {}
    for cost in (True, False):
        run_id = uuid.uuid4().hex
        since = snowflake_since(sf)
        try:
            res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, obs, cost=cost)
            assert res.success, res.exception
            nodes = node_results(run_results)
            rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=3 * len(nodes) + 2,
                                      since=since)
        finally:
            drop_fixture_views(sf, run_id)
        out[cost] = rows
    return out


def cost_rows(rows):
    return [r for r in rows if any(k.startswith("dbt.cost.") for k in r["record_attributes"])
            or r["record_attributes"].get("dbt.metric.name") == "credits"]


@pytest.mark.sandbox
def test_var_cost_false_writes_no_cost_record(builds):
    assert cost_rows(builds[False]) == []


@pytest.mark.sandbox
def test_var_cost_true_never_fails_the_run_when_attribution_lags(builds):
    # the run succeeded (fixture). Right after it QUERY_ATTRIBUTION_HISTORY usually has not got its queries,
    # so usually there is nothing; whatever did land carries credits.
    for r in builds[True]:
        if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "cost":
            assert r["record_attributes"]["dbt.cost.credits"] >= 0


# --- backfill (sandbox) ---------------------------------------------------------------------------

def seed_log(inv, unique_id, query_id=None, kind="node_result", **attrs):
    a = {"icecore.kind": kind, "dbt.invocation_id": inv, "dbt.node.unique_id": unique_id,
         "dbt.node.type": "model", "dbt.node.name": unique_id.split(".")[-1],
         "dbt.node.relation": f'"ICECORE_SANDBOX"."SPIKES"."{unique_id.split(".")[-1].upper()}"', **attrs}
    if query_id:
        a["snow.query.id"] = query_id
    return {"type": "log", "level": "info", "message": f"{unique_id} {kind}", "attrs": a}


def seed_query_span(inv, unique_id, query_id):
    now = time.time_ns()
    return {"name": "icecore.node", "start_ns": now, "end_ns": now, "attrs": {"dbt.invocation_id": inv, "dbt.node.unique_id": unique_id},
            "events": [{"name": "icecore.query", "ts_ns": now,
                        "attrs": {"dbt.invocation_id": inv, "snow.query.id": query_id, "dbt.node.unique_id": unique_id}}]}


@pytest.fixture(scope="module")
def earlier_run(sf, obs, old_queries):
    """An earlier invocation whose costs were never recorded: n1 (one query), n2 (two queries, one from a node
    result and one from a query event), n3 already costed, n4 with a query that is not attributed."""
    inv = f"old-{uuid.uuid4().hex}"
    q1, q2, q3, q4 = list(old_queries)
    since = snowflake_since(sf)
    items = [seed_log(inv, "model.p.n1", q1), seed_log(inv, "model.p.n2", q2), seed_query_span(inv, "model.p.n2", q3),
             seed_log(inv, "model.p.n3", q4), seed_log(inv, "model.p.n3", kind="cost", **{"dbt.cost.credits": 9.0}),
             seed_log(inv, "model.p.n4", "01c00000-0000-0000-0000-000000000000")]
    extra = ["--vars", json.dumps({"icecore": {"obs_schema": obs}})]
    run_macro("it_emit_items", {"items": items}, extra)
    invocation_records(sf, obs, inv, min_rows=7, since=since)
    return {"inv": inv, "since": since, "queries": old_queries, "ids": [q1, q2, q3, q4]}


def build_with_cost(sf, obs, since, **settings):
    run_id = uuid.uuid4().hex
    try:
        res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, obs, cost=True, **settings)
        assert res.success, res.exception
        return run_results["metadata"]["invocation_id"]
    finally:
        drop_fixture_views(sf, run_id)


def landed_costs(sf, obs, earlier_run, min_rows, settle_s=20):
    rows = invocation_records(sf, obs, earlier_run["inv"], min_rows=min_rows, since=earlier_run["since"], settle_s=settle_s)
    logs = [r["record_attributes"] for r in rows
            if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "cost"]
    metrics = [r["record_attributes"] for r in rows
               if r["record_type"] == "SPAN_EVENT" and r["record"].get("name") == "icecore.metric"]
    return logs, metrics


@pytest.mark.sandbox
def test_next_run_backfills_earlier_nodes_once_without_duplicates(sf, obs, earlier_run):
    q = earlier_run["queries"]
    q1, q2, q3, q4 = earlier_run["ids"]
    new_invocation = build_with_cost(sf, obs, earlier_run["since"])
    # seeded rows: 6 + 1 query event = 7; backfill adds 2 cost logs, 2 spans and 2 metric events on the original run
    logs, metrics = landed_costs(sf, obs, earlier_run, min_rows=7 + 2 + 2 + 2)
    backfilled = {a["dbt.node.unique_id"]: a for a in logs if a.get("icecore.backfill")}
    assert sorted(backfilled) == ["model.p.n1", "model.p.n2"]
    assert backfilled["model.p.n1"]["dbt.cost.credits"] == pytest.approx(q[q1][1])
    assert backfilled["model.p.n2"]["dbt.cost.credits"] == pytest.approx(q[q2][1] + q[q3][1])
    assert all(a["dbt.invocation_id"] == earlier_run["inv"] and a["icecore.backfilled_by"] == new_invocation
               for a in backfilled.values())
    assert backfilled["model.p.n1"]["dbt.cost.warehouse"] == q[q1][0]
    by_node = {m["dbt.node.unique_id"]: m for m in metrics if m["dbt.metric.name"] == "credits"}
    assert sorted(by_node) == ["model.p.n1", "model.p.n2"]
    assert by_node["model.p.n1"]["dbt.metric.value"] == pytest.approx(q[q1][1])
    # n3 (already costed) kept its one record; n4 (not attributed) has none
    assert [a["dbt.cost.credits"] for a in logs if a["dbt.node.unique_id"] == "model.p.n3"] == [9.0]
    assert not [a for a in logs if a["dbt.node.unique_id"] == "model.p.n4"]

    # the next run finds nothing new for them: no duplicate
    since = snowflake_since(sf, 2)
    build_with_cost(sf, obs, since)
    time.sleep(60)
    logs, metrics = landed_costs(sf, obs, earlier_run, min_rows=7 + 2 + 2 + 2)
    assert sorted(a["dbt.node.unique_id"] for a in logs) == ["model.p.n1", "model.p.n2", "model.p.n3"]
    assert len([m for m in metrics if m["dbt.metric.name"] == "credits"]) == 2

    # n4's query never gets an attribution row: once settled (cost_settle_hours 0 here, 24 by default) it is closed
    # at 0 credits with its reason, has no metric, and the next run leaves it alone
    settling = build_with_cost(sf, obs, since, cost_settle_hours=0)
    logs, metrics = landed_costs(sf, obs, earlier_run, min_rows=7 + 2 + 2 + 2 + 1)
    settled = [a for a in logs if a["dbt.node.unique_id"] == "model.p.n4"]
    assert len(settled) == 1 and settled[0]["dbt.cost.credits"] == 0.0
    assert settled[0]["dbt.cost.settled"] == "no_attribution" and settled[0]["icecore.backfilled_by"] == settling
    assert settled[0]["dbt.cost.node_at"].endswith("Z")
    assert not [m for m in metrics if m["dbt.node.unique_id"] == "model.p.n4"]
    build_with_cost(sf, obs, since, cost_settle_hours=0)
    time.sleep(60)
    logs, _ = landed_costs(sf, obs, earlier_run, min_rows=7 + 2 + 2 + 2 + 1)
    assert len([a for a in logs if a["dbt.node.unique_id"] == "model.p.n4"]) == 1


def attach_with(obs, results, **settings):
    extra = ["--vars", json.dumps({"icecore": {"obs_schema": obs, "cost": True, **settings}})]
    return run_macro_with_logs("it_attach_cost_run", {"results": results}, extra)


def regression_run(sf, obs, old_queries, min_rows, **settings):
    """Two nodes with 14 days of nearly stable credits each (a stand-in METRICS view); the second node's credits are
    5x its history. Returns (relations, landed metric events, landed anomaly events, logs)."""
    run_id = uuid.uuid4().hex[:8]
    since = snowflake_since(sf)
    q_stable, q_jump = list(old_queries)[:2]
    names = {"stable": f"cost_stable_{run_id}", "jump": f"cost_jump_{run_id}"}
    rel = {k: f'"ICECORE_SANDBOX"."SPIKES"."{v.upper()}"' for k, v in names.items()}
    base = {"stable": old_queries[q_stable][1], "jump": old_queries[q_jump][1] / 5}
    qs = f'"ICECORE_SANDBOX"."{obs}"'
    rows = []
    for key in ("stable", "jump"):
        for day in range(1, 15):
            value = base[key] * (1 + 0.004 * ((day % 3) - 1))
            rows.append(f"select '{rel[key]}', 'credits', null::string, null::string, {value!r}::float,"
                        f" dateadd(day, -{day}, convert_timezone('UTC', current_timestamp())::timestamp_ntz)")
    sf.cursor().execute(f'create or replace view {qs}."METRICS" as select * from (' + " union all ".join(rows)
                        + ') as t(relation, metric, "COLUMN", dimension, value, observed_at)')
    results = [result_for(f"model.p.{names['stable']}", q_stable), result_for(f"model.p.{names['jump']}", q_jump)]
    out, logs = attach_with(obs, results, **settings)
    landed = invocation_records(sf, obs, out["invocation_id"], min_rows=min_rows, since=since)
    keyed = lambda name: {r["record_attributes"]["dbt.node.relation"]: r["record_attributes"] for r in landed  # noqa: E731
                          if r["record_type"] == "SPAN_EVENT" and r["record"].get("name") == name}
    return rel, keyed("icecore.metric"), keyed("icecore.anomaly"), logs


@pytest.mark.sandbox
def test_five_fold_jump_in_credits_is_a_regression_and_stable_credits_are_not(sf, obs, old_queries):
    rel, metrics, anomalies, _ = regression_run(sf, obs, old_queries, min_rows=5)
    assert metrics[rel["jump"]]["dbt.anomaly.is_anomaly"] is True
    assert metrics[rel["stable"]]["dbt.anomaly.is_anomaly"] is False
    assert list(anomalies) == [rel["jump"]]
    assert anomalies[rel["jump"]]["dbt.metric.name"] == "credits" and anomalies[rel["jump"]]["dbt.anomaly.score"] > 0


@pytest.mark.sandbox
def test_at_most_cost_score_max_nodes_are_scored_newest_first(sf, obs, old_queries):
    # cap 1: the newest node (the jump, last in the results) is scored, the older one is written unscored
    rel, metrics, anomalies, logs = regression_run(sf, obs, old_queries, min_rows=4, cost_score_max_nodes=1)
    assert metrics[rel["jump"]]["dbt.anomaly.is_anomaly"] is True
    assert "dbt.anomaly.is_anomaly" not in metrics[rel["stable"]]
    assert any("cost scoring: 1 of 2 nodes scored" in line for line in logs), logs


def test_history_points_query_is_one_query_over_the_metrics_view_for_all_keys():
    sql = run_macro("it_credit_history_points_sql", {"keys": ['"DB"."S"."A"', '"DB"."S"."B"'], "delay": 1})
    assert sql.count("select") == 2 and "group by relation" in sql
    assert '."METRICS"' in sql and "metric = 'credits'" in sql
    assert "relation in ('\"DB\".\"S\".\"A\"', '\"DB\".\"S\".\"B\"')" in sql
    assert "dateadd(day, -30," in sql and "dateadd(minute, -1," in sql


def test_cost_score_off_or_capped_at_zero_scores_nothing_and_queries_nothing():
    points = [{"key": "a", "credits": 1.0}, {"key": "b", "credits": 2.0}]
    off = run_macro("it_score_costs", {"points": points}, ["--vars", json.dumps({"icecore": {"cost_score": False}})])
    assert off == {"scored": {}, "left": None}
    zero = run_macro("it_score_costs", {"points": points}, ["--vars", json.dumps({"icecore": {"cost_score_max_nodes": 0}})])
    assert zero == {"scored": {}, "left": 0}


def _vars(**icecore):
    return ["--vars", json.dumps({"icecore": icecore})]


def test_same_node_from_several_invocations_is_scored_once_per_invocation():
    pending = [{"invocation_id": "i1", "unique_id": "n.a", "relation": '"DB"."S"."A"'},
               {"invocation_id": "i2", "unique_id": "n.a", "relation": '"DB"."S"."A"'}]
    cost = {"credits": 2.0, "warehouse": "WH"}
    points = run_macro("it_backfill_points", {"pending": pending, "costs": {"i1|n.a": cost, "i2|n.a": cost}})
    assert [p["id"] for p in points] == ["i1|n.a", "i2|n.a"] and {p["key"] for p in points} == {'"DB"."S"."A"'}
    out = run_macro("it_score_costs_stubbed", {"points": points})
    assert sorted(out["scored"]) == ["i1|n.a", "i2|n.a"]  # one result each: no last-write-wins
    assert all(r["is_anomaly"] is True and r["score"] == 5.0 and r["engine"] == "native" for r in out["scored"].values())


def test_settled_backfill_points_are_not_scored():
    pending = [{"invocation_id": "i1", "unique_id": "n.a", "relation": "r"}]
    out = run_macro("it_backfill_points", {"pending": pending, "costs": {"i1|n.a": {"credits": 0.0, "settled": "no_attribution"}}})
    assert out == []


def test_invalid_anomaly_settings_skip_scoring_with_one_warning_and_never_raise():
    points = [{"key": "a", "credits": 1.0}]
    ok = run_macro("it_score_costs_cfg", {"points": points, "cfg": {"anomaly_sensitivity": "high"}})
    assert list(ok["scored"]) == ["a"]  # valid settings score (stub runner)
    for bad in ({"anomaly_engine": "prophet"}, {"anomaly_sensitivity": "extreme"}, {"anomaly_min_consecutive": 0},
                {"anomaly_min_consecutive": "2"}, {"anomaly_mad_floor_pct": "x"}):
        out, logs = run_macro_with_logs("it_score_costs_cfg", {"points": points, "cfg": bad})
        assert out == {"scored": {}, "left": None}, bad
        assert any("cost scoring skipped" in line for line in logs), bad


def test_a_failing_native_query_scores_that_point_unscored_with_a_warning_and_never_raises():
    points = [{"id": "ok", "key": "k-ok", "credits": 1.0}, {"id": "bad", "key": "k-FAIL", "credits": 1.0}]
    out, logs = run_macro_with_logs("it_score_costs_stubbed", {"points": points})
    assert sorted(out["scored"]) == ["ok"]
    assert sum("cost scoring failed for 1 of 2" in line for line in logs) == 1


def test_negative_cost_score_max_nodes_counts_as_zero():
    points = [{"key": "a", "credits": 1.0}]
    out = run_macro("it_score_costs", {"points": points}, _vars(cost_score_max_nodes=-5))
    assert out == {"scored": {}, "left": 0}
