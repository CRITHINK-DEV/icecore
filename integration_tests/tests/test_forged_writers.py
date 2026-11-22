"""Records written by a procedure that only looks like an icecore writer are ignored.

Snowflake names a procedure in the event table as `NAME(ARG TYPE, ...):RETURN`, so a role that can create procedures in the
obs schema could add an overload (ICECORE_ACK(k VARCHAR, x NUMBER)) that a prefix filter would accept. The views match the
full signature of each real procedure (macros/views/_base_events.sql).
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, run_macro
from helpers.run_hooks import snowflake_since

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
KEY = "0" * 32
REAL = {"ICECORE_EMIT(EVENTS ARRAY):VARCHAR", "ICECORE_NOTIFIED(INCIDENT_KEY VARCHAR, CHANNEL VARCHAR):VARCHAR"}


def _forger(schema, signature, event, attrs):
    return (f'create or replace procedure "{DATABASE}"."{schema}".{signature} returns string language sql execute as owner '
            f"as $$ begin system$add_event('{event}', object_construct({attrs})); return 'forged'; end; $$")


@pytest.mark.sandbox
def test_an_overload_of_a_writer_is_not_a_writer(dbt, sf):
    schema = f"ICECORE_OBS_{uuid.uuid4().hex}".upper()
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        since = snowflake_since(sf, 1)
        for sql in (
            _forger(schema, "ICECORE_ACK(k varchar, x number)", "icecore.ack", f"'icecore.ack.incident_key', '{KEY}', 'icecore.ack.by', 'forger'"),
            _forger(schema, "ICECORE_NOTIFIED(k varchar, c varchar, x number)", "icecore.notified",
                    f"'icecore.notified.incident_key', '{KEY}', 'icecore.notified.channel', 'forged'"),
            _forger(schema, "ICECORE_EMIT(x number)", "icecore.metric", "'dbt.metric.name', 'forged'")):
            cur.execute(sql)
        for call in ("ICECORE_ACK('k', 1)", "ICECORE_NOTIFIED('k', 'c', 1)", "ICECORE_EMIT(1)",
                     f"ICECORE_NOTIFIED('{KEY}', 'real')", "ICECORE_EMIT(parse_json('[{\"name\": \"icecore.real\", \"attrs\": {}}]'))"):
            cur.execute(f'call "{DATABASE}"."{schema}".{call}')
        raw = wait_for_events(sf, 'resource_attributes:"snow.schema.name"::string = %(schema)s and record_type = \'SPAN_EVENT\'',
                              {"schema": schema}, min_rows=5, since=since, settle_s=10, timeout_s=300)
        names = {r["resource_attributes"]["snow.executable.name"] for r in raw}
        assert REAL <= names and {n for n in names if n.startswith("ICECORE_ACK(")} == {"ICECORE_ACK(K VARCHAR, X NUMBER):VARCHAR"}, names
        view = f'"{DATABASE}"."{schema}"."BASE_EVENTS"'
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        rows = sf.cursor(DictCursor).execute(f"select record_name, executable_name from {view} where event_at >= %(s)s", {"s": since}).fetchall()
        assert {(r["RECORD_NAME"], r["EXECUTABLE_NAME"]) for r in rows} == {
            ("icecore.notified", "ICECORE_NOTIFIED(INCIDENT_KEY VARCHAR, CHANNEL VARCHAR):VARCHAR"),
            ("icecore.real", "ICECORE_EMIT(EVENTS ARRAY):VARCHAR")}, rows
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
