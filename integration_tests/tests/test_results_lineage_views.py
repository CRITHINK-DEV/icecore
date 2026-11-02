"""UC5 views `test_results`, `lineage`, `lineage_latest` and `capabilities` (macros/views/), appended to
icecore.create_views' list on top of #36's `metrics` and `schema_changes`.

Offline tests render the SQL. The sandbox test creates the emitter procedures and the views in a run_id-named schema,
ICECORE_OBS_<run_id>, runs a tiny dbt build with the hooks (hooks_a+, whose one generic test warns with 3 failures)
for the node-result LOGs, sends `icecore.kind = lineage` LOGs in the record shape of story/uc1-lineage (commit
c793437, macros/telemetry/emit_lineage.sql) through ICECORE_EMIT_RUN, records the capabilities twice with
icecore.detect_capabilities, queries the views and drops the schema at the end.
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import run_macro
from helpers.run_hooks import (
    drop_fixture_views, icecore_vars, invocation_records, node_results, obs_schema, run_with_hooks, snowflake_since,
)

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
EVENT_TABLE = "DB.TELEMETRY.EVENTS"


def views(schema=None) -> dict:
    return {v["name"]: v["sql"] for v in run_macro("it_views_render", {"schema": schema})}


# --- offline: rendering only, no connection ------------------------------------------------------

def test_create_views_list_is_the_full_current_list():
    """The one place that asserts the whole list, in order; every other test checks a prefix or membership."""
    assert list(views()) == ["METRICS", "SCHEMA_CHANGES", "TEST_RESULTS", "LINEAGE", "LINEAGE_LATEST", "CAPABILITIES",
                             "INVOCATIONS", "NODE_EXECUTIONS", "SILENCE_EVENTS", "ALERTS_FEED"]


@pytest.mark.parametrize("name", ["TEST_RESULTS", "LINEAGE", "LINEAGE_LATEST", "CAPABILITIES"])
def test_view_selects_from_base_events_on_its_schema(name):
    sql = views("my_obs")[name]
    assert "with events as (" in sql and f"from {EVENT_TABLE}" in sql
    assert "RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG')" in sql and "ICECORE_EMIT%" in sql
    assert """"snow.schema.name"::string = 'MY_OBS'""" in sql


def test_test_results_reads_node_result_logs_of_tests():
    sql = views()["TEST_RESULTS"]
    assert "record_type = 'LOG'" in sql and """attributes:"icecore.kind"::string = 'node_result'""" in sql
    assert "node_unique_id like 'test.%'" in sql
    for column in ["test_name", "test_attached_node as tested_unique_id", 'test_column as "COLUMN"', "test_severity as severity", "result_status as status",
                   "test_failures as failures", "result_message as message", "event_at as executed_at"]:
        assert column in sql, column
    assert "partition by invocation_id, node_unique_id order by event_at desc) = 1" in sql


def test_test_results_and_node_executions_expose_the_new_columns():
    assert "test_attached_node as tested_unique_id" in views()["TEST_RESULTS"]
    assert "node_result.root_cause_kind as root_cause_kind" in views()["NODE_EXECUTIONS"]


def test_lineage_flattens_the_edge_lists_of_lineage_logs():
    sql = views()["LINEAGE"]
    assert """attributes:"icecore.kind"::string = 'lineage'""" in sql
    assert "lateral flatten(input => try_parse_json(" in sql and '"dbt.lineage.edges"' in sql
    assert "edge.value[0]::string as parent_unique_id" in sql and "edge.value[1]::string as child_unique_id" in sql
    assert "dense_rank" not in sql


def test_lineage_latest_keeps_each_projects_latest_invocation():
    sql = views()["LINEAGE_LATEST"]
    assert "qualify dense_rank() over (partition by project order by invocation_at desc, invocation_id desc) = 1" in sql


def test_capabilities_reads_the_capability_keys_and_keeps_latest_per_database():
    sql = views()["CAPABILITIES"]
    assert "record_name = 'icecore.capabilities'" in sql
    for key in ["database", "edition", "edition_source", "event_table", "event_table_level", "cortex", "cortex_reason",
                "log_level", "trace_level"]:
        assert f'"icecore.capability.{key}"' in sql, key
    assert "partition by database_name order by event_at desc) = 1" in sql


# --- sandbox --------------------------------------------------------------------------------------

EDGES_OLD = [["model.p.a", "model.p.b"], ["model.p.b", "model.p.c"], ["source.p.s.t", "model.p.a"]]
EDGES_NEW = [["model.p.a", "model.p.b"], ["model.p.b", "model.p.d"]]


def lineage_logs(run_id: str, edges: list, parts: int = 1) -> list:
    """LOG items in the shape of emit_lineage (c793437): edges split over `parts` records."""
    edges = [[f"{u}_{run_id}" for u in pair] for pair in edges]
    chunks = [edges[i::parts] for i in range(parts)]
    return [{"type": "log", "level": "info",
             "message": f"dbt lineage {n}/{parts}: {len(c)} of {len(edges)} edges",
             "attrs": {"icecore.kind": "lineage", "dbt.lineage.edges": json.dumps(c), "dbt.lineage.edge_count": len(c),
                       "dbt.lineage.edges_total": len(edges), "dbt.lineage.part": n, "dbt.lineage.parts": parts}}
            for n, c in enumerate(chunks, 1)]


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    run_id = uuid.uuid4().hex
    with obs_schema(sf) as schema:
        obs_vars = ["--vars", json.dumps(icecore_vars(schema))]
        since = snowflake_since(sf)
        try:
            res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, schema)
            assert res.success, res.exception
            nodes = node_results(run_results)
            invocation = run_results["metadata"]["invocation_id"]
            rows = invocation_records(sf, schema, invocation, min_rows=3 * len(nodes) + 2, since=since)
        finally:
            drop_fixture_views(sf, run_id)
        # a failed test with a root cause, in the record shape root cause writes (emit_root_cause.sql)
        run_macro("it_emit_run_items", {"items": [{
            "type": "log", "level": "error", "message": "rc",
            "attrs": {"icecore.kind": "node_result", "dbt.node.unique_id": f"test.p.rc_{run_id}", "dbt.node.type": "test",
                      "dbt.result.status": "fail", "dbt.root_cause.unique_id": f"source.p.s.t_{run_id}",
                      "dbt.root_cause.kind": "stale_source", "dbt.invocation_id": "rc-" + run_id}}]}, obs_vars)
        # lineage: an old two-record emission, then a newer single-record one (a later invocation each)
        run_macro("it_emit_run_items", {"items": lineage_logs(run_id, EDGES_OLD, parts=2)}, obs_vars)
        time.sleep(3)
        run_macro("it_emit_run_items", {"items": lineage_logs(run_id, EDGES_NEW)}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'LOG'"
                " and record_attributes:\"icecore.kind\"::string = 'lineage'",
            {"db": DATABASE, "schema": schema}, min_rows=3, since=since, settle_s=10)
        created = run_macro("it_create_views", {"schema": schema}, obs_vars)
        yield {"run_id": run_id, "schema": schema, "created": created, "nodes": nodes, "invocation": invocation,
               "since": since}


def _rows(sf, seeded, view: str, where: str = "true", params=None) -> list:
    sql = f'select * from "{DATABASE}"."{seeded["schema"]}"."{view}" where {where}'
    return [{k.lower(): v for k, v in r.items()}
            for r in sf.cursor(DictCursor).execute(sql, params or {}).fetchall()]


@pytest.mark.sandbox
def test_create_views_creates_the_views_of_this_file(seeded):
    created = [n.rsplit(".", 1)[1].strip('"') for n in seeded["created"]]
    assert created[:6] == ["METRICS", "SCHEMA_CHANGES", "TEST_RESULTS", "LINEAGE", "LINEAGE_LATEST", "CAPABILITIES"]


@pytest.mark.sandbox
def test_warning_test_is_one_row_with_three_failures(sf, seeded):
    rows = _rows(sf, seeded, "TEST_RESULTS", "invocation_id = %(i)s", {"i": seeded["invocation"]})
    assert len(rows) == 1 and rows[0]["invocation_id"] == seeded["invocation"]
    r = rows[0]
    assert r["test_unique_id"].startswith("test.") and r["test_name"] == "accepted_values"
    assert (r["test_type"], r["column"], r["severity"], r["status"], r["failures"]) == ("generic", "id", "warn", "warn", 3)
    assert r["executed_at"] and r["query_id"] and r["execution_time_s"] is not None
    assert r["engine"] is None and r["score"] is None
    expected = next(n for n in seeded["nodes"] if n["unique_id"].startswith("test."))
    assert r["test_unique_id"] == expected["unique_id"]
    # the test is attached to hooks_c: the real attached_node of the real dbt build
    assert r["tested_unique_id"] == "model.icecore_integration_tests.hooks_c", r["tested_unique_id"]
    assert r["tested_unique_id"] in {n["unique_id"] for n in seeded["nodes"]}


@pytest.mark.sandbox
def test_node_executions_exposes_root_cause_kind(sf, seeded):
    rid = seeded["run_id"]
    (r,) = _rows(sf, seeded, "NODE_EXECUTIONS", "unique_id = %(u)s", {"u": f"test.p.rc_{rid}"})
    assert r["root_cause_unique_id"] == f"source.p.s.t_{rid}" and r["root_cause_kind"] == "stale_source"
    models = _rows(sf, seeded, "NODE_EXECUTIONS", "type = 'model' and root_cause_unique_id is null")
    assert models and all(m["root_cause_kind"] is None for m in models)


@pytest.mark.sandbox
def test_test_results_hold_no_model_results(sf, seeded):
    assert _rows(sf, seeded, "TEST_RESULTS", "test_unique_id not like 'test.%'") == []


@pytest.mark.sandbox
def test_lineage_has_one_row_per_edge_per_invocation_whatever_the_split(sf, seeded):
    rid = seeded["run_id"]
    rows = _rows(sf, seeded, "LINEAGE", "parent_unique_id like %(p)s or child_unique_id like %(p)s", {"p": f"%{rid}"})
    assert len(rows) == len(EDGES_OLD) + len(EDGES_NEW)
    by_invocation = {}
    for r in rows:
        by_invocation.setdefault(r["invocation_id"], set()).add((r["parent_unique_id"], r["child_unique_id"]))
    old, new = sorted(by_invocation.values(), key=len, reverse=True)
    assert old == {(f"{a}_{rid}", f"{b}_{rid}") for a, b in EDGES_OLD}
    assert new == {(f"{a}_{rid}", f"{b}_{rid}") for a, b in EDGES_NEW}
    assert all(r["recorded_at"] for r in rows)


@pytest.mark.sandbox
def test_lineage_latest_is_the_newest_edge_set_only(sf, seeded):
    rid = seeded["run_id"]
    rows = _rows(sf, seeded, "LINEAGE_LATEST")
    assert {(r["parent_unique_id"], r["child_unique_id"]) for r in rows} == {
        (f"{a}_{rid}", f"{b}_{rid}") for a, b in EDGES_NEW}
    assert len({r["invocation_id"] for r in rows}) == 1


@pytest.mark.sandbox
def test_capabilities_keeps_the_latest_record_of_the_database(sf, seeded, dbt):
    obs_vars = ["--vars", json.dumps(icecore_vars(seeded["schema"]))]
    for _ in range(2):
        run_macro("it_detect_capabilities", {}, obs_vars)
        time.sleep(3)
    wait_for_events(
        sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
            " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
            " and record:name::string = 'icecore.capabilities'",
        {"db": DATABASE, "schema": seeded["schema"]}, min_rows=2, since=seeded["since"], settle_s=10)
    raw = sf.cursor().execute(
        f'select count(*) from "{DATABASE}"."{seeded["schema"]}"."CAPABILITIES"').fetchone()[0]
    (r,) = _rows(sf, seeded, "CAPABILITIES")
    assert raw == 1
    assert r["database_name"] == DATABASE and r["edition"] == "ENTERPRISE" and r["cortex_available"] is True
    assert r["event_table"] == "ICECORE_SANDBOX.TELEMETRY.EVENTS" and r["event_table_level"] == "DATABASE"
    assert r["log_level"] and r["trace_level"] and r["detected_at"] and r["invocation_id"]
