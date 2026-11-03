"""UC5 run views over base_events: `invocations` (macros/views/invocations.sql) and `node_executions`
(macros/views/node_executions.sql), created by icecore.create_views.

Offline tests render the SQL. The sandbox fixture runs real `dbt build`s with the icecore hooks (helpers/run_hooks.py)
into an obs schema of its own, ICECORE_OBS_<id>: hooks_a+ (ends `warn`: the accepted_values test on hooks_c warns)
and hooks_a with a failing hooks_fail (ends `error`). It also writes one `icecore.run_start` and no end, a run that
never finished. Then it creates the views in that schema and reads them. The schema is dropped at the end.

Times: the views take started_at / ended_at from dbt's clock (dbt.invocation.* and dbt.node.* attributes), never
from the TIMESTAMP of the records, which is Snowflake's.
"""
import json
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.macros import run_macro
from helpers.run_hooks import (
    DATABASE, drop_fixture_views, icecore_vars, invocation_records, node_results, obs_schema, run_with_hooks,
    snowflake_since,
)
from helpers.events import wait_for_events


def views() -> dict:
    return {v["name"]: v["sql"] for v in run_macro("it_views_render", {})}


# --- offline: rendering only, no connection ------------------------------------------------------

def test_both_views_are_in_the_create_views_list_after_the_earlier_ones():
    names = list(views())
    assert names[6:8] == ["INVOCATIONS", "NODE_EXECUTIONS"]  # the full list is asserted in test_results_lineage_views


def test_invocations_reads_the_three_run_records_on_the_invocation_id():
    sql = views()["INVOCATIONS"]
    assert "with events as (" in sql and "ICECORE_EMIT%" in sql
    for part in ["record_name = 'icecore.run_start'", "record_name = 'icecore.invocation'",
                 """attributes:"icecore.kind"::string = 'run_end'""", """"dbt.invocation.status"::string""",
                 """"dbt.invocation.nodes_total\"""", """"dbt.invocation.nodes_failed\"""",
                 "'running'", "'unfinished'", "sum(cost_credits)", "as credits"]:
        assert part in sql, part


def test_node_executions_reads_span_result_and_query_events():
    sql = views()["NODE_EXECUTIONS"]
    for part in ["record_name = 'icecore.node'", """attributes:"icecore.kind"::string = 'node_result'""",
                 "record_name = 'icecore.query'", "partition by invocation_id, node_unique_id",
                 "node_result.node_query_id", "as query_id", "as credits", "as credits_settled", "as root_cause_unique_id",
                 """attributes:"icecore.kind"::string = 'cost'""", """"dbt.cost.settled\""""]:
        assert part in sql, part
    assert "null::float as credits" not in views()["INVOCATIONS"]
    assert sql.index("as credits,") < sql.index("as credits_settled,") < sql.rindex("as root_cause_unique_id")


def test_times_come_from_dbts_attributes_not_from_the_record_timestamp():
    for name in ("INVOCATIONS", "NODE_EXECUTIONS"):
        sql = views()[name]
        assert "try_to_timestamp_ntz" in sql
        assert "as started_at" in sql and "event_at as started_at" not in sql, name
    assert "datediff" not in views()["INVOCATIONS"] and "datediff" not in views()["NODE_EXECUTIONS"]


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built(sf):
    run_id = uuid.uuid4().hex
    try:
        with obs_schema(sf) as schema:
            since = snowflake_since(sf)
            ok, ok_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, schema)
            assert ok.success, ok.exception
            bad, bad_results = run_with_hooks(["build", "--select", "hooks_a", "hooks_fail"], run_id, schema,
                                              extra_vars={"hooks_fail": True})
            assert not bad.success
            ids = {"ok": ok_results["metadata"]["invocation_id"], "bad": bad_results["metadata"]["invocation_id"],
                   "open": f"it-open-{run_id}"}
            run_macro("it_emit_events", {"events": [{"kind": "run_start", "attributes": {
                "dbt.invocation_id": ids["open"], "dbt.command": "build", "dbt.runtime": "core",
                "dbt.invocation.started_at": "2026-01-01T00:00:00.000000Z"}}]},
                ["--vars", json.dumps(icecore_vars(schema))])
            for key, results in (("ok", ok_results), ("bad", bad_results)):
                invocation_records(sf, schema, ids[key], min_rows=3 + 2 * len(node_results(results)), since=since)
            wait_for_events(sf, "record_attributes:\"dbt.invocation_id\"::string = %(i)s", {"i": ids["open"]},
                            since=since, settle_s=5)
            run_macro("it_create_views", {"schema": schema}, ["--vars", json.dumps(icecore_vars(schema))])
            yield {"schema": schema, "ids": ids, "ok_results": ok_results, "bad_results": bad_results}
    finally:
        drop_fixture_views(sf, run_id)


def _rows(sf, built, view: str, invocation_id: str) -> list:
    sql = (f'select * from "{DATABASE}"."{built["schema"]}"."{view}" where invocation_id = %(i)s '
           f'order by {"started_at" if view == "INVOCATIONS" else "unique_id"}')
    return [{k.lower(): v for k, v in r.items()}
            for r in sf.cursor(DictCursor).execute(sql, {"i": invocation_id}).fetchall()]


@pytest.mark.sandbox
def test_a_build_is_one_invocation_row_with_status_and_counts(sf, built):
    (row,) = _rows(sf, built, "INVOCATIONS", built["ids"]["ok"])
    assert row["command"] == "build" and row["runtime"] == "core" and row["status"] == "warn"
    assert row["nodes_total"] == len(node_results(built["ok_results"])) and row["nodes_failed"] == 0
    assert row["nodes_warned"] == 1 and row["credits"] is None
    assert row["started_at"] and row["ended_at"] and row["ended_at"] >= row["started_at"]  # dbt's clock, both
    assert row["duration_s"] > 0 and row["dbt_version"]


@pytest.mark.sandbox
def test_a_failed_run_is_error_with_one_failed_node(sf, built):
    (row,) = _rows(sf, built, "INVOCATIONS", built["ids"]["bad"])
    assert (row["status"], row["nodes_failed"]) == ("error", 1)


@pytest.mark.sandbox
def test_a_run_with_a_start_and_no_end_is_running(sf, built):
    (row,) = _rows(sf, built, "INVOCATIONS", built["ids"]["open"])
    assert row["status"] == "running" and row["ended_at"] is None and row["nodes_total"] is None
    assert row["command"] == "build" and str(row["started_at"]).startswith("2026-01-01 00:00:00")


@pytest.mark.sandbox
def test_one_node_row_per_node_with_a_query_id(sf, built):
    rows = _rows(sf, built, "NODE_EXECUTIONS", built["ids"]["ok"])
    expected = sorted(r["unique_id"] for r in node_results(built["ok_results"]))
    assert [r["unique_id"] for r in rows] == expected
    models = [r for r in rows if r["type"] == "model"]
    assert models and all(r["query_id"] and r["query_ids"] for r in models), models
    for r in rows:
        assert r["status"] and r["started_at"] and r["completed_at"] and r["credits"] is None
        assert r["root_cause_unique_id"] is None
    assert {r["unique_id"]: r["status"] for r in rows} == {
        r["unique_id"]: r["status"] for r in node_results(built["ok_results"])}


@pytest.mark.sandbox
def test_a_failed_node_row_has_status_error(sf, built):
    rows = _rows(sf, built, "NODE_EXECUTIONS", built["ids"]["bad"])
    assert sorted(r["status"] for r in rows) == ["error", "success"]


# --- credits from the cost records (sandbox) -----------------------------------------------------

def _node_item(inv: str, uid: str) -> list:
    base = {"dbt.invocation_id": inv, "dbt.node.unique_id": uid, "dbt.node.type": "model", "dbt.node.name": uid}
    return [{"type": "log", "level": "info", "message": f"{uid} result",
             "attrs": {**base, "icecore.kind": "node_result", "dbt.result.status": "success"}}]


def _cost_item(inv: str, uid: str, credits: float, settled: str | None = None, backfill: bool = False) -> dict:
    attrs = {"icecore.kind": "cost", "dbt.invocation_id": inv, "dbt.node.unique_id": uid, "dbt.cost.credits": credits}
    if settled:
        attrs["dbt.cost.settled"] = settled
    if backfill:
        attrs["icecore.backfill"] = True
    return {"type": "log", "level": "info", "message": f"{uid} cost: {credits} credits", "attrs": attrs}


@pytest.fixture(scope="module")
def costed(sf):
    """One invocation with four nodes: live cost, backfilled cost, settled (0), and settled first then really costed."""
    run_id = uuid.uuid4().hex
    inv = f"it-cost-{run_id}"
    nodes = {k: f"model.it.{k}_{run_id}" for k in ("live", "back", "settled", "upgraded", "none")}
    with obs_schema(sf) as schema:
        obs_vars = ["--vars", json.dumps(icecore_vars(schema))]
        since = snowflake_since(sf)
        items = [i for u in nodes.values() for i in _node_item(inv, u)]
        items.append({"type": "log", "level": "info", "message": "run end",
                      "attrs": {"icecore.kind": "run_end", "dbt.invocation_id": inv, "dbt.command": "build",
                                "dbt.invocation.status": "success"}})
        run_macro("it_emit_run_items", {"items": items + [_cost_item(inv, nodes["live"], 0.5),
                                                          _cost_item(inv, nodes["settled"], 0.0, "no_attribution"),
                                                          _cost_item(inv, nodes["upgraded"], 0.0, "no_attribution")]}, obs_vars)
        time.sleep(3)
        # later runs: the backfill (keyed to the original invocation) and a real cost after a settle
        run_macro("it_emit_run_items", {"items": [_cost_item(inv, nodes["back"], 0.25, backfill=True),
                                                  _cost_item(inv, nodes["upgraded"], 0.125, backfill=True)]}, obs_vars)
        wait_for_events(sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                            " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'LOG'"
                            " and record_attributes:\"dbt.invocation_id\"::string = %(i)s",
                        {"db": DATABASE, "schema": schema, "i": inv}, min_rows=len(nodes) + 6, since=since, settle_s=8)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        yield {"schema": schema, "inv": inv, "nodes": nodes}


@pytest.mark.sandbox
def test_node_executions_credits_come_from_the_latest_cost_record_per_node(sf, costed):
    rows = {r["unique_id"]: r for r in _rows(sf, costed, "NODE_EXECUTIONS", costed["inv"])}
    n = costed["nodes"]
    assert (rows[n["live"]]["credits"], rows[n["live"]]["credits_settled"]) == (0.5, None)
    assert (rows[n["back"]]["credits"], rows[n["back"]]["credits_settled"]) == (0.25, None)
    assert (rows[n["settled"]]["credits"], rows[n["settled"]]["credits_settled"]) == (0.0, "no_attribution")
    assert (rows[n["upgraded"]]["credits"], rows[n["upgraded"]]["credits_settled"]) == (0.125, None)  # latest wins
    assert (rows[n["none"]]["credits"], rows[n["none"]]["credits_settled"]) == (None, None)


@pytest.mark.sandbox
def test_invocations_credits_is_the_sum_of_the_node_credits(sf, costed):
    (row,) = _rows(sf, costed, "INVOCATIONS", costed["inv"])
    assert row["credits"] == pytest.approx(0.5 + 0.25 + 0.0 + 0.125)
