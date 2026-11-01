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


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture(scope="module")
def old_queries(sf):
    """Three attributed queries older than 12 hours: {query_id: (warehouse, credits)}."""
    rows = sf.cursor().execute(
        "select query_id, warehouse_name, credits_attributed_compute + coalesce(credits_used_query_acceleration, 0)"
        " from snowflake.account_usage.query_attribution_history"
        " where start_time between dateadd(day, -6, current_timestamp()) and dateadd(hour, -12, current_timestamp())"
        " and credits_attributed_compute > 0 order by start_time limit 3").fetchall()
    assert len(rows) == 3, "the sandbox has no attributed query from previous days"
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
    ids = list(old_queries)
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
