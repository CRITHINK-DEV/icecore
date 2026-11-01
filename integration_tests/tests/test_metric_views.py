"""UC5 views over base_events: `metrics` (macros/views/metrics.sql), `schema_changes` (schema_changes.sql) and
icecore.create_views (create_views.sql), the setup step that creates them.

Offline tests render the SQL. The sandbox test creates the emitter procedures and the views in a run_id-named schema,
ICECORE_OBS_<run_id> (passed as the obs_schema var, so that schema is the run tag), seeds records with
icecore.emit_metric (two row_count volume metrics, one scored anomalous metric) and icecore.emit_events (two
`icecore.schema_change` events, the keys the schema_changes view documents), queries the views and drops the
schema at the end.
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
EVENT_TABLE = "DB.TELEMETRY.EVENTS"


def views(schema=None) -> dict:
    return {v["name"]: v["sql"] for v in run_macro("it_views_render", {"schema": schema})}


# --- offline: rendering only, no connection ------------------------------------------------------

def test_create_views_lists_metrics_and_schema_changes_first():
    assert list(views())[:2] == ["METRICS", "SCHEMA_CHANGES"]


def test_every_view_selects_from_base_events():
    for name, sql in views().items():
        assert f"from {EVENT_TABLE}" in sql, name  # base_events' own SELECT, on the resolved table
        assert "with events as (" in sql and "RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG')" in sql, name
        assert "ICECORE_EMIT%" in sql, name


def test_views_filter_on_the_schema_they_are_created_in():
    for name, sql in views("my_obs").items():
        assert """"snow.schema.name"::string = 'MY_OBS'""" in sql, name


def test_metrics_reads_the_keys_emit_metric_writes():
    sql = views()["METRICS"]
    assert "record_name = 'icecore.metric'" in sql
    for column in ["node_relation as relation", 'metric_column as "COLUMN"', "metric_dimension as dimension",
                   "metric_name as metric", "event_at as observed_at", "metric_value as value", "anomaly_engine",
                   "anomaly_expected", "anomaly_lower", "anomaly_upper", "anomaly_score", "anomaly_is_anomaly"]:
        assert column in sql, column


def test_schema_changes_reads_the_spec_keys():
    sql = views()["SCHEMA_CHANGES"]
    assert "record_name = 'icecore.schema_change'" in sql
    for key in ["dbt.schema.change_kind", "dbt.schema.column", "dbt.schema.from_type", "dbt.schema.to_type"]:
        assert f'attributes:"{key}"::string' in sql, key
    assert "node_relation as relation" in sql and "event_at as detected_at" in sql


def test_view_ddl_keeps_grants_and_qualifies_the_name():
    ddl = run_macro("it_view_ddl", {"view": "METRICS", "sql": "select 1", "schema": "my_obs"})
    assert ddl.startswith('CREATE OR REPLACE VIEW "') and '"MY_OBS"."METRICS" COPY GRANTS AS' in ddl


def test_setup_marks_create_views_ready():
    steps = {s["name"]: s for s in call_macro("_icecore_setup_steps")}
    assert steps["create_views"]["ready"] is True


# --- sandbox --------------------------------------------------------------------------------------

def _rel(run_id: str, name: str) -> dict:
    return {"database": DATABASE, "schema": "SPIKES", "identifier": f"it_view_{name}_{run_id}", "quoted": True}


def _qualified(run_id: str, name: str) -> str:
    return f'"{DATABASE}"."SPIKES"."it_view_{name}_{run_id}"'


SCORED = {"engine": "native", "expected": 1000.0, "lower": 900.0, "upper": 1100.0, "score": -3.2,
          "is_anomaly": True, "fallback_reason": None}


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        since = cur.execute("select dateadd(minute, -2, convert_timezone('UTC', current_timestamp())::timestamp_ntz)"
                            ).fetchone()[0]
        vol = _rel(run_id, "volume")
        run_macro("it_emit_metric", {"rel": vol, "metric": "row_count", "value": 100}, obs_vars)
        run_macro("it_emit_metric", {"rel": vol, "metric": "row_count", "value": 110}, obs_vars)
        run_macro("it_emit_metric", {"rel": _rel(run_id, "scored"), "metric": "null_rate", "value": 0.02,
                                     "column": "amount", "dimension": "country=FR", "anomaly": SCORED}, obs_vars)
        changed = _qualified(run_id, "changed")
        run_macro("it_emit_events", {"events": [
            {"kind": "schema_change", "attributes": {
                "dbt.node.relation": changed, "dbt.schema.change_kind": "added", "dbt.schema.column": "NEW_COL",
                "dbt.schema.to_type": "NUMBER(38,0)"}},
            {"kind": "schema_change", "attributes": {
                "dbt.node.relation": changed, "dbt.schema.change_kind": "type_changed", "dbt.schema.column": "AMOUNT",
                "dbt.schema.from_type": "NUMBER(10,2)", "dbt.schema.to_type": "FLOAT"}},
        ]}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=6, since=since, settle_s=10)
        created = run_macro("it_create_views", {"schema": schema}, obs_vars)
        yield {"run_id": run_id, "schema": schema, "created": created}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def _query(sf, seeded, view: str, relation_like: str) -> list:
    sql = (f'select * from "{DATABASE}"."{seeded["schema"]}"."{view}" where relation like %(rel)s '
           f"order by observed_at" if view == "METRICS" else
           f'select * from "{DATABASE}"."{seeded["schema"]}"."{view}" where relation like %(rel)s order by detected_at')
    rows = sf.cursor(DictCursor).execute(sql, {"rel": f"%{relation_like}%"}).fetchall()
    return [{k.lower(): v for k, v in r.items()} for r in rows]


@pytest.mark.sandbox
def test_create_views_returns_both_qualified_names(seeded):
    assert seeded["created"][:2] == [f'"{DATABASE}"."{seeded["schema"]}"."METRICS"',
                                     f'"{DATABASE}"."{seeded["schema"]}"."SCHEMA_CHANGES"']


@pytest.mark.sandbox
def test_volume_metric_twice_gives_two_row_count_rows(sf, seeded):
    rows = _query(sf, seeded, "METRICS", f"it_view_volume_{seeded['run_id']}")
    assert [(r["metric"], r["value"]) for r in rows] == [("row_count", 100.0), ("row_count", 110.0)]
    assert all(r["relation"] == _qualified(seeded["run_id"], "volume") and r["observed_at"] for r in rows)


@pytest.mark.sandbox
def test_unscored_metrics_have_null_anomaly_fields_and_the_view_succeeds(sf, seeded):
    rows = _query(sf, seeded, "METRICS", f"it_view_volume_{seeded['run_id']}")
    for r in rows:
        assert all(r[k] is None for k in ["anomaly_engine", "anomaly_expected", "anomaly_lower", "anomaly_upper",
                                          "anomaly_score", "anomaly_is_anomaly"]), r


@pytest.mark.sandbox
def test_scored_metric_is_one_row_even_with_an_anomaly_event(sf, seeded):
    (r,) = _query(sf, seeded, "METRICS", f"it_view_scored_{seeded['run_id']}")
    assert (r["metric"], r["column"], r["dimension"], r["value"]) == ("null_rate", "amount", "country=FR", 0.02)
    assert (r["anomaly_engine"], r["anomaly_expected"], r["anomaly_lower"], r["anomaly_upper"], r["anomaly_score"],
            r["anomaly_is_anomaly"]) == ("native", 1000.0, 900.0, 1100.0, -3.2, True)


@pytest.mark.sandbox
def test_column_added_between_runs_gives_one_added_row(sf, seeded):
    rows = _query(sf, seeded, "SCHEMA_CHANGES", f"it_view_changed_{seeded['run_id']}")
    added = [r for r in rows if r["change_kind"] == "added"]
    assert len(added) == 1
    assert (added[0]["column"], added[0]["from_type"], added[0]["to_type"]) == ("NEW_COL", None, "NUMBER(38,0)")
    assert added[0]["detected_at"] and added[0]["relation"] == _qualified(seeded["run_id"], "changed")
    (changed,) = [r for r in rows if r["change_kind"] == "type_changed"]
    assert (changed["column"], changed["from_type"], changed["to_type"]) == ("AMOUNT", "NUMBER(10,2)", "FLOAT")


@pytest.mark.sandbox
def test_views_hold_only_their_own_record_kinds(sf, seeded):
    cur = sf.cursor()
    m = cur.execute(f'select count(*) from "{DATABASE}"."{seeded["schema"]}"."METRICS"').fetchone()[0]
    s = cur.execute(f'select count(*) from "{DATABASE}"."{seeded["schema"]}"."SCHEMA_CHANGES"').fetchone()[0]
    assert (m, s) == (3, 2)
