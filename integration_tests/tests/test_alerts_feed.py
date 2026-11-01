"""UC6 alerts_feed view (macros/views/alerts_feed.sql) and icecore.acknowledge (macros/alerts/acknowledge.sql).

Offline tests render the SQL. The sandbox test creates the emitter procedures and the views in a run_id-named schema,
ICECORE_OBS_<run_id>; seeds a failed test (two `hooks_a+` builds, so two consecutive runs of the same failing test),
one anomalous metric (icecore.emit_metric with a scored result) and one schema change; reads the feed; acknowledges
the anomaly with icecore.acknowledge; and drops the schema at the end.
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import MacroFailed, run_macro, run_macro_with_logs
from helpers.run_hooks import (
    drop_fixture_views, icecore_vars, invocation_records, obs_schema, run_with_hooks, snowflake_since,
)

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()


def feed_sql(*icecore_vars_: dict, schema=None) -> str:
    extra = ["--vars", json.dumps({"icecore": icecore_vars_[0]})] if icecore_vars_ else []
    out = run_macro_with_logs("it_views_render", {"schema": schema}, extra)[0]
    return {v["name"]: v["sql"] for v in out}["ALERTS_FEED"]


# --- offline: rendering only, no connection ------------------------------------------------------

def test_alerts_feed_is_appended_to_create_views():
    names = [v["name"] for v in run_macro("it_views_render", {"schema": None})]
    assert names[-1] == "ALERTS_FEED"


def test_feed_reads_base_events_and_unions_the_three_live_signals():
    sql = feed_sql()
    assert "from DB.TELEMETRY.EVENTS" in sql and "ICECORE_EMIT%" in sql
    for needle in ["'node_result'", "record_name = 'icecore.metric'", "record_name = 'icecore.schema_change'",
                   "where status in ('fail', 'error', 'warn')", "where anomaly_is_anomaly = true",
                   "record_name = 'icecore.ack'", '"icecore.ack.incident_key"', '"icecore.ack.by"']:
        assert needle in sql, needle


def test_silence_and_cost_slots_are_empty_ctes_with_the_signal_columns():
    sql = feed_sql()
    for slot, story in [("silence_events", "uc6-silence"), ("cost_regressions", "cost-regression")]:
        head, _, rest = sql.partition(f"{slot} as (")
        body = rest.split("\n),", 1)[0]
        assert "where false" in body, slot
        for column in ["as kind", "as node_key", "as severity", "as title", "as detected_at", "as invocation_id"]:
            assert column in body, (slot, column)
        assert story in head.split("-- EMPTY SLOT")[-1], slot
    assert "from silence_events" in sql and "from cost_regressions" in sql


def test_incident_key_hashes_node_kind_and_window():
    sql = feed_sql()
    assert "md5(node_key || '|' || kind || '|' || window_epoch::string) as incident_key" in sql
    assert "group by node_key, kind, window_epoch" in sql


@pytest.mark.parametrize("setting, seconds", [({}, 86400), ({"alert_window_hours": 1}, 3600),
                                              ({"alert_window_hours": 0.5}, 1800), ({"alert_window_hours": 0}, 86400),
                                              ({"alert_window_hours": "junk"}, 86400)])
def test_window_length_is_a_var_default_24_hours(setting, seconds):
    sql = feed_sql(setting)
    assert f"/ {seconds}) * {seconds} as window_epoch" in sql


def test_acknowledge_needs_an_incident_key():
    with pytest.raises(MacroFailed, match="incident_key"):
        run_macro_with_logs("it_call", {"name": "acknowledge", "kwargs": {"incident_key": ""}})


# --- sandbox --------------------------------------------------------------------------------------

SCORED = {"engine": "native", "expected": 1000.0, "lower": 900.0, "upper": 1100.0, "score": -3.2,
          "is_anomaly": True, "fallback_reason": None}


def _feed(sf, schema: str, where: str = "true", params=None) -> list:
    sql = f'select * from "{DATABASE}"."{schema}"."ALERTS_FEED" where {where} order by kind'
    return [{k.lower(): v for k, v in r.items()} for r in sf.cursor(DictCursor).execute(sql, params or {}).fetchall()]


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    run_id = uuid.uuid4().hex
    with obs_schema(sf) as schema:
        obs_vars = ["--vars", json.dumps(icecore_vars(schema))]
        since = snowflake_since(sf)
        invocations = []
        try:
            for _ in range(2):  # the same failing test on two consecutive runs
                res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, schema)
                assert res.success, res.exception
                invocations.append(run_results["metadata"]["invocation_id"])
                invocation_records(sf, schema, invocations[-1], min_rows=14, since=since, settle_s=5)
        finally:
            drop_fixture_views(sf, run_id)
        rel = {"database": DATABASE, "schema": "SPIKES", "identifier": f"it_feed_{run_id}", "quoted": True}
        relation = f'"{DATABASE}"."SPIKES"."it_feed_{run_id}"'
        run_macro("it_emit_metric", {"rel": rel, "metric": "row_count", "value": 100}, obs_vars)  # not anomalous
        run_macro("it_emit_metric", {"rel": rel, "metric": "null_rate", "value": 0.5, "column": "amount",
                                     "anomaly": SCORED}, obs_vars)
        run_macro("it_emit_events", {"events": [{"kind": "schema_change", "attributes": {
            "dbt.node.relation": relation, "dbt.schema.change_kind": "added", "dbt.schema.column": "NEW_COL",
            "dbt.schema.to_type": "NUMBER(38,0)"}}]}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'"
                " and record:name::string in ('icecore.metric', 'icecore.schema_change')",
            {"db": DATABASE, "schema": schema}, min_rows=3, since=since, settle_s=10)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        yield {"schema": schema, "obs_vars": obs_vars, "run_id": run_id, "invocations": invocations, "since": since,
               "relation": relation}


@pytest.mark.sandbox
def test_failed_test_and_anomaly_are_two_rows_with_distinct_keys(seeded, sf):
    rows = _feed(sf, seeded["schema"], "kind in ('test_failure', 'anomaly')")
    assert sorted(r["kind"] for r in rows) == ["anomaly", "test_failure"]
    assert len({r["incident_key"] for r in rows}) == 2
    assert all(r["acknowledged_at"] is None and r["acknowledged_by"] is None for r in rows)


@pytest.mark.sandbox
def test_only_anomalous_metrics_and_one_schema_change_are_in_the_feed(seeded, sf):
    rows = _feed(sf, seeded["schema"], "node_key like %(r)s", {"r": f"%it_feed_{seeded['run_id']}%"})
    assert sorted(r["kind"] for r in rows) == ["anomaly", "schema_change"]
    anomaly = next(r for r in rows if r["kind"] == "anomaly")
    assert "null_rate" in anomaly["node_key"] and "row_count" not in anomaly["node_key"]


@pytest.mark.sandbox
def test_same_failure_on_two_runs_in_one_window_is_one_key(seeded, sf):
    rows = _feed(sf, seeded["schema"], "kind = 'test_failure'")
    assert len(rows) == 1
    r = rows[0]
    assert r["occurrences"] == 2 and r["last_invocation_id"] in seeded["invocations"]
    assert r["first_detected_at"] < r["last_detected_at"] and r["severity"] == "warn"
    assert r["node_key"].startswith("test.")


@pytest.mark.sandbox
def test_ack_sets_acknowledged_at_and_by_on_that_key_only(seeded, sf):
    anomaly = _feed(sf, seeded["schema"], "kind = 'anomaly'")[0]
    since = snowflake_since(sf, 1)
    out = run_macro_with_logs("it_call", {"name": "acknowledge", "kwargs": {
        "incident_key": anomaly["incident_key"], "by": "azbaida"}}, seeded["obs_vars"])[0]
    assert out["statuses"] == ["ok 1"], out
    wait_for_events(
        sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.ack'",
        {"schema": seeded["schema"]}, since=since, settle_s=5)
    rows = {r["kind"]: r for r in _feed(sf, seeded["schema"])}
    assert rows["anomaly"]["acknowledged_at"] is not None and rows["anomaly"]["acknowledged_by"] == "azbaida"
    assert rows["test_failure"]["acknowledged_at"] is None and rows["schema_change"]["acknowledged_at"] is None
