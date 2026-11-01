"""UC6 dedup_window (macros/control/dedup_window.sql): what an alert may still notify.

Offline tests render the SQL. The sandbox test creates the emitter procedures and the views in a run_id-named schema,
ICECORE_OBS_<run_id>; seeds three schema-change incidents (icecore.emit_events), records a notification and an
acknowledgement the way the alert and icecore.acknowledge do, and runs the dedup SELECT, once against the live event
table and once against a copy of it whose `icecore.notified` rows are moved back in time (a notification older than
the window). The copy is a table in SPIKES named with the run_id, dropped at the end.
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import run_macro, run_macro_with_logs
from helpers.run_hooks import icecore_vars, obs_schema, snowflake_since

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
EVENT_TABLE = "DB.TELEMETRY.EVENTS"


def dedup(channel="email", schema=None, event_table=EVENT_TABLE, extra=None, **kwargs) -> str:
    kw = {"channel": channel, "schema": schema, "event_table": event_table, **kwargs}
    return run_macro_with_logs("it_call", {"name": "dedup_window_sql", "kwargs": kw}, extra)[0]


def minutes(settings=None) -> int:
    extra = ["--vars", json.dumps({"icecore": settings})] if settings else None
    return run_macro_with_logs("it_call", {"name": "dedup_window_minutes", "kwargs": {}}, extra)[0]


# --- offline: rendering only, no connection ------------------------------------------------------

def test_dedup_reads_the_feed_and_the_notified_events_through_base_events():
    sql = dedup(schema="my_obs")
    assert f"from {EVENT_TABLE}" in sql and "ICECORE_EMIT%" in sql
    assert """"snow.schema.name"::string = 'MY_OBS'""" in sql
    assert f'"{DATABASE}"."MY_OBS"."ALERTS_FEED" as feed' in sql
    assert "record_name = 'icecore.notified'" in sql
    assert '"icecore.notified.incident_key"' in sql and '"icecore.notified.channel"' in sql


def test_acknowledged_incidents_are_never_returned():
    assert "where feed.acknowledged_at is null" in dedup()


def test_notification_suppression_is_per_channel_and_inside_the_window():
    sql = dedup("slack", window_minutes=90)
    assert "and notified.channel = 'slack'" in sql
    assert "notified.last_notified_at < dateadd(minute, -90, " in sql
    assert "notified.last_notified_at is null" in sql


@pytest.mark.parametrize("settings, expected", [
    (None, 1440), ({"alert_window_hours": 2}, 120), ({"alert_dedup_minutes": 30}, 30),
    ({"alert_dedup_minutes": 0}, 1440), ({"alert_dedup_minutes": "junk"}, 1440), ({"alert_dedup_minutes": 0.2}, 1)])
def test_window_defaults_to_the_incident_window(settings, expected):
    assert minutes(settings) == expected


def test_notifications_are_recorded_through_emit_never_inserted():
    sql = dedup()
    assert "insert" not in sql.lower() and "merge" not in sql.lower()


# --- sandbox --------------------------------------------------------------------------------------

def _change(relation: str, column: str) -> dict:
    return {"kind": "schema_change", "attributes": {
        "dbt.node.relation": relation, "dbt.schema.change_kind": "added", "dbt.schema.column": column,
        "dbt.schema.to_type": "NUMBER(38,0)"}}


def _pending(sf, schema: str, channel: str, event_table: str, window=60) -> list:
    sql = dedup(channel, schema, event_table, window_minutes=window, extra=["--vars", json.dumps(icecore_vars(schema))])
    return [{k.lower(): v for k, v in r.items()} for r in sf.cursor(DictCursor).execute(sql).fetchall()]


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    run_id = uuid.uuid4().hex
    table = f"{DATABASE}.TELEMETRY.EVENTS"
    with obs_schema(sf) as schema:
        obs_vars = ["--vars", json.dumps(icecore_vars(schema))]
        since = snowflake_since(sf)
        rel = f'"{DATABASE}"."SPIKES"."it_dedup_{run_id}"'
        run_macro("it_emit_events", {"events": [_change(rel, "A"), _change(rel, "B")]}, obs_vars)
        wait_for_events(sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                            " and record:name::string = 'icecore.schema_change'",
                        {"schema": schema}, min_rows=2, since=since, settle_s=5)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        fetched = sf.cursor(DictCursor).execute(f'select node_key, incident_key from "{DATABASE}"."{schema}"."ALERTS_FEED"').fetchall()
        keys = {r["NODE_KEY"].split("|")[1]: r["INCIDENT_KEY"] for r in fetched}
        copy = f'"{DATABASE}"."SPIKES"."it_dedup_events_{run_id}"'
        yield {"schema": schema, "obs_vars": obs_vars, "keys": keys, "table": table, "copy": copy, "since": since,
               "run_id": run_id}
        sf.cursor().execute(f"drop table if exists {copy}")


def notify(seeded, key, channel):
    run_macro("it_emit_events", {"events": [{"kind": "notified", "attributes": {
        "icecore.notified.incident_key": key, "icecore.notified.channel": channel}}]}, seeded["obs_vars"])


def wait_notified(sf, seeded, n):
    wait_for_events(sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                        " and record:name::string = 'icecore.notified'", {"schema": seeded["schema"]},
                    min_rows=n, since=seeded["since"], settle_s=5)


@pytest.mark.sandbox
def test_01_new_incidents_are_all_pending(sf, seeded):
    assert sorted(seeded["keys"]) == ["A", "B"]
    rows = _pending(sf, seeded["schema"], "email", seeded["table"])
    assert sorted(r["incident_key"] for r in rows) == sorted(seeded["keys"].values())
    assert all(r["last_notified_at"] is None for r in rows)


@pytest.mark.sandbox
def test_02_notified_incident_is_not_notified_again_inside_the_window_but_other_channels_are(sf, seeded):
    notify(seeded, seeded["keys"]["A"], "email")
    wait_notified(sf, seeded, 1)
    email = _pending(sf, seeded["schema"], "email", seeded["table"])
    assert [r["incident_key"] for r in email] == [seeded["keys"]["B"]]
    slack = _pending(sf, seeded["schema"], "slack", seeded["table"])
    assert len(slack) == 2


@pytest.mark.sandbox
def test_03_acknowledged_incident_is_not_notified_even_after_the_window(sf, seeded):
    key = seeded["keys"]["B"]
    out = run_macro_with_logs("it_call", {"name": "acknowledge", "kwargs": {"incident_key": key}}, seeded["obs_vars"])[0]
    assert out == "ok 1", out
    wait_for_events(sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                        " and record:name::string = 'icecore.ack'", {"schema": seeded["schema"]},
                    since=seeded["since"], settle_s=5)
    notify(seeded, key, "email")
    wait_notified(sf, seeded, 2)
    cur = sf.cursor()
    cur.execute(f"create table {seeded['copy']} as select * from {seeded['table']}"
                " where RESOURCE_ATTRIBUTES:\"snow.schema.name\"::string = %s", (seeded["schema"],))
    cur.execute(f"update {seeded['copy']} set TIMESTAMP = dateadd(day, -3, TIMESTAMP)"
                " where RECORD:name::string = 'icecore.notified'")
    old = _pending(sf, seeded["schema"], "email", seeded["copy"])
    # A was notified 3 days ago and is still open: a reminder; B is acknowledged: nothing, whatever the age
    assert [r["incident_key"] for r in old] == [seeded["keys"]["A"]]


@pytest.mark.sandbox
def test_04_new_incident_key_after_an_acknowledgement_is_notified(sf, seeded):
    rel = f'"{DATABASE}"."SPIKES"."it_dedup_{seeded["run_id"]}"'
    since = snowflake_since(sf, 1)
    run_macro("it_emit_events", {"events": [_change(rel, "C")]}, seeded["obs_vars"])
    wait_for_events(sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                        " and record:name::string = 'icecore.schema_change'", {"schema": seeded["schema"]},
                    min_rows=3, since=seeded["since"], settle_s=5)
    rows = _pending(sf, seeded["schema"], "email", seeded["table"])
    assert len(rows) == 1 and rows[0]["node_key"].endswith("|C")
    assert rows[0]["incident_key"] not in seeded["keys"].values()
