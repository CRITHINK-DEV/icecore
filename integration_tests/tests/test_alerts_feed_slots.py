"""UC6 alerts_feed silence and cost signals (macros/views/alerts_feed.sql, story 'missed runs and cost regressions in
the alerts feed').

Sandbox: in a run_id schema ICECORE_OBS_<id> a silence verdict is seeded through ICECORE_EMIT as the silence task
writes it (the same event name and keys), twice for one missed run, plus a credits anomaly above the band, one below
it, and a non-credits anomaly. The feed, the acknowledgement and the dedup select (what the alert notifies) are read.
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
EVENT_TABLE = f"{DATABASE}.TELEMETRY.EVENTS"

ABOVE = {"engine": "native", "expected": 1.0, "lower": 0.5, "upper": 2.0, "score": 4.0, "is_anomaly": True,
         "fallback_reason": None}
BELOW = {"engine": "native", "expected": 1.0, "lower": 0.5, "upper": 2.0, "score": -4.0, "is_anomaly": True,
         "fallback_reason": None}


def _silence(unique_id: str, expected_at: str, severity: str) -> dict:
    return {"kind": "silence", "attributes": {
        "dbt.node.unique_id": unique_id, "icecore.silence.expected_at": expected_at,
        "icecore.silence.last_success_at": "2026-11-01T08:00:00Z", "icecore.silence.expected_every_minutes": 60,
        "icecore.silence.grace_minutes": 1, "icecore.silence.channel": "data-platform",
        "icecore.silence.severity": severity}}


def _feed(sf, schema: str, where: str = "true", params=None) -> list:
    sql = f'select * from "{DATABASE}"."{schema}"."ALERTS_FEED" where {where} order by kind, node_key'
    return [{k.lower(): v for k, v in r.items()} for r in sf.cursor(DictCursor).execute(sql, params or {}).fetchall()]


def _pending(sf, schema: str, obs_vars) -> list:
    sql = run_macro_with_logs("it_call", {"name": "dedup_window_sql", "kwargs": {
        "channel": "email", "schema": schema, "event_table": EVENT_TABLE, "window_minutes": 60}}, obs_vars)[0]
    return [{k.lower(): v for k, v in r.items()} for r in sf.cursor(DictCursor).execute(sql).fetchall()]


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    run_id = uuid.uuid4().hex
    with obs_schema(sf) as schema:
        obs_vars = ["--vars", json.dumps(icecore_vars(schema))]
        since = snowflake_since(sf)
        node = f"model.it.late_{run_id}"
        other = f"model.it.other_{run_id}"
        rel = lambda n: {"database": DATABASE, "schema": "SPIKES", "identifier": f"it_slot_{n}_{run_id}", "quoted": True}  # noqa: E731
        q = lambda n: f'"{DATABASE}"."SPIKES"."it_slot_{n}_{run_id}"'  # noqa: E731
        # the same missed run checked twice by the task, one error verdict for another node
        run_macro("it_emit_events", {"events": [_silence(node, "2026-11-01T09:00:00Z", "error"),
                                                _silence(node, "2026-11-01T09:00:00Z", "error")]}, obs_vars)
        run_macro("it_emit_events", {"events": [_silence(other, "2026-11-01T10:00:00Z", "warn")]}, obs_vars)
        run_macro("it_emit_metric", {"rel": rel("up"), "metric": "credits", "value": 5.0, "anomaly": ABOVE}, obs_vars)
        run_macro("it_emit_metric", {"rel": rel("down"), "metric": "credits", "value": 0.1, "anomaly": BELOW}, obs_vars)
        run_macro("it_emit_metric", {"rel": rel("rows"), "metric": "row_count", "value": 9.0, "anomaly": ABOVE}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'"
                " and record:name::string in ('icecore.silence', 'icecore.metric', 'icecore.anomaly')",
            {"schema": schema}, min_rows=6, since=since, settle_s=10)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        yield {"schema": schema, "obs_vars": obs_vars, "node": node, "other": other, "q": q, "since": since}


@pytest.mark.sandbox
def test_silence_verdict_is_one_silence_incident_for_the_node(seeded, sf):
    rows = _feed(sf, seeded["schema"], "kind = 'silence' and node_key = %(n)s", {"n": seeded["node"]})
    assert len(rows) == 1
    r = rows[0]
    assert r["severity"] == "error" and r["last_invocation_id"] is None
    assert seeded["node"] in r["title"] and "2026-11-01 09:00:00" in r["title"]


@pytest.mark.sandbox
def test_the_same_missed_run_checked_twice_is_still_one_incident_with_its_occurrences(seeded, sf):
    (r,) = _feed(sf, seeded["schema"], "kind = 'silence' and node_key = %(n)s", {"n": seeded["node"]})
    assert r["occurrences"] == 2
    assert r["first_detected_at"] <= r["last_detected_at"]
    (o,) = _feed(sf, seeded["schema"], "kind = 'silence' and node_key = %(n)s", {"n": seeded["other"]})
    assert o["severity"] == "warn" and o["occurrences"] == 1 and o["incident_key"] != r["incident_key"]


@pytest.mark.sandbox
def test_a_cost_regression_appears_once_not_twice_through_the_anomaly_signal(seeded, sf):
    up = seeded["q"]("up")
    rows = _feed(sf, seeded["schema"], "node_key like %(r)s", {"r": f"{up}|credits|%"})
    assert [r["kind"] for r in rows] == ["cost"], rows
    assert rows[0]["severity"] == "warn" and "credits" in rows[0]["title"] and " are 5 " in rows[0]["title"]
    # a credits drop is no regression and raises nothing; other metrics still go through the anomaly signal
    assert _feed(sf, seeded["schema"], "node_key like %(r)s", {"r": f"{seeded['q']('down')}|credits|%"}) == []
    rows = _feed(sf, seeded["schema"], "node_key like %(r)s", {"r": f"{seeded['q']('rows')}|row_count|%"})
    assert [r["kind"] for r in rows] == ["anomaly"]


@pytest.mark.sandbox
def test_an_acknowledged_silence_incident_is_not_notified_by_the_alert(seeded, sf):
    pending = {r["node_key"] for r in _pending(sf, seeded["schema"], seeded["obs_vars"])}
    assert {seeded["node"], seeded["other"]} <= pending  # the alert routes silence like any incident (default channel)
    (r,) = _feed(sf, seeded["schema"], "kind = 'silence' and node_key = %(n)s", {"n": seeded["node"]})
    since = snowflake_since(sf, 1)
    out = run_macro_with_logs("it_call", {"name": "acknowledge", "kwargs": {"incident_key": r["incident_key"]}},
                              seeded["obs_vars"])[0]
    assert out == "ok 1", out
    wait_for_events(
        sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.ack'",
        {"schema": seeded["schema"]}, since=since, settle_s=5)
    (acked,) = _feed(sf, seeded["schema"], "incident_key = %(k)s", {"k": r["incident_key"]})
    assert acked["acknowledged_at"] is not None
    pending = {p["node_key"] for p in _pending(sf, seeded["schema"], seeded["obs_vars"])}
    assert seeded["node"] not in pending and seeded["other"] in pending
