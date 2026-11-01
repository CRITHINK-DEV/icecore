"""UC6 render_alert_sql and create_alerts (macros/control/render_alert_sql.sql): one ALERT per channel.

Offline tests render the SQL and the validation. The sandbox test creates the emitter procedures and the views in a
run_id-named schema, ICECORE_OBS_<run_id>; seeds incidents whose names hold quotes, backslashes, $$, ; and newlines (and
a statement that would create a canary table); creates the alerts (suspended); runs each alert's condition query and
the action with the notification CALL left out, because the sandbox has no notification integration: what could not
run is the SYSTEM$SEND_SNOWFLAKE_NOTIFICATION call itself. Alerts are never resumed; the schema is dropped at the end.
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import assert_no_events, wait_for_events
from helpers.macros import MacroFailed, run_macro, run_macro_with_logs
from helpers.run_hooks import icecore_vars, obs_schema, snowflake_since

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
EVENT_TABLE = "DB.TELEMETRY.EVENTS"

EMAIL = {"name": "email", "type": "email", "integration": "ICECORE_EMAIL_INT", "recipients": ["data@example.com"],
         "default": True}
SLACK = {"name": "chat", "type": "slack", "integration": "ICECORE_SLACK_INT", "recipients": [], "default": False}
TEAMS = {"name": "teams", "type": "teams", "integration": "ICECORE_TEAMS_INT", "recipients": [], "default": False}


def call(name: str, kwargs: dict, icecore_settings: dict | None = None):
    extra = ["--vars", json.dumps({"icecore": icecore_settings})] if icecore_settings else None
    return run_macro_with_logs("it_call", {"name": name, "kwargs": kwargs}, extra)[0]


def render(channel, entries=(), all_channels=None, **kwargs) -> str:
    return call("render_alert_sql", {"channel": channel, "entries": list(entries), "event_table": EVENT_TABLE,
                                     "warehouse": "WH", "all_channels": all_channels or [channel], **kwargs})


def entry(unique_id, channel, severity="warn"):
    return {"unique_id": unique_id, "channel": channel, "severity": severity}


# --- offline: rendering only, no connection ------------------------------------------------------

def test_email_alert_condition_is_the_dedup_select_and_action_sends_then_records():
    sql = render(EMAIL, schema="my_obs")
    assert sql.startswith('CREATE OR REPLACE ALERT "' + DATABASE + '"."MY_OBS"."ICECORE_ALERT_EMAIL"')
    assert "WAREHOUSE = WH" in sql and "SCHEDULE = '5 MINUTE'" in sql and "IF (EXISTS (" in sql
    assert f'from "{DATABASE}"."MY_OBS"."ALERTS_FEED"' in sql and "and notified.channel = 'email'" in sql
    assert "where feed.acknowledged_at is null" in sql
    assert "SNOWFLAKE.ALERT.GET_CONDITION_QUERY_UUID()" in sql and "result_scan(:qid)" in sql
    assert ("SNOWFLAKE.NOTIFICATION.EMAIL_INTEGRATION_CONFIG('ICECORE_EMAIL_INT', 'icecore: new incidents', "
            "ARRAY_CONSTRUCT('data@example.com'))") in sql
    assert "CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(:msg)" in sql
    assert f'CALL "{DATABASE}"."MY_OBS"."ICECORE_NOTIFIED"(:k, \'email\');' in sql
    assert '"ICECORE_EMIT"' not in sql  # icecore.notified is refused by the emitters
    assert sql.index("SEND_SNOWFLAKE_NOTIFICATION") < sql.index('"ICECORE_NOTIFIED"(:k')
    assert "insert" not in sql.lower() and "$$" not in sql and "execute immediate" not in sql.lower()


@pytest.mark.parametrize("channel, integration", [(SLACK, "ICECORE_SLACK_INT"), (TEAMS, "ICECORE_TEAMS_INT")])
def test_webhook_alerts_sanitize_the_text_and_use_the_integration(channel, integration):
    sql = render(channel)
    assert f"SNOWFLAKE.NOTIFICATION.INTEGRATION('{integration}')" in sql
    assert "TEXT_PLAIN(SNOWFLAKE.NOTIFICATION.SANITIZE_WEBHOOK_CONTENT(:msg))" in sql


@pytest.mark.parametrize("type, marker", [("email", "' - root cause: '"), ("slack", "', root cause: `'"),
                                          ("teams", "', root cause: '")])
def test_message_names_node_kind_and_root_cause_in_each_format(type, marker):
    sql = call("alert_message_sql", {"type": type, "source": "pending"})
    for column in ["kind", "title", "node_key", "root_cause_unique_id", "root_cause_kind"]:
        assert column in sql, column
    assert marker in sql and "limit 50" in sql and "left(" in sql
    assert "*" in sql if type == "slack" else True
    assert "**" in sql if type == "teams" else True


def test_the_record_loop_binds_the_incident_key_and_names_a_literal_channel():
    sql = call("alert_record_sql", {"channel_name": "email", "source": "pending"})
    assert "LET rs RESULTSET := (select incident_key from pending order by first_detected_at, incident_key limit 50);" in sql
    assert "LET c CURSOR FOR rs;" in sql
    assert "FOR r IN c DO" in sql
    assert "k := r.incident_key;" in sql and "ICECORE_NOTIFIED\"(:k, 'email');" in sql and "limit 50" in sql


def test_routing_sends_a_claimed_node_to_its_channel_and_the_rest_to_the_default():
    entries = [entry("model.p.a", "chat"), entry("model.p.b", "chat", "error"), entry("model.p.c", "email")]
    both = [EMAIL, SLACK]
    chat = render(SLACK, entries, both)
    assert "((node_key = 'model.p.a') or (node_key = 'model.p.b' and severity = 'error'))" in chat
    assert "not in" not in chat
    email = render(EMAIL, entries, both)
    assert "((node_key = 'model.p.c') or node_key not in ('model.p.a', 'model.p.b', 'model.p.c'))" in email


def test_a_channel_nobody_claims_and_that_is_not_default_matches_nothing():
    assert "where (false)" in render(SLACK, [], [EMAIL, SLACK])


def test_default_channel_with_nothing_claimed_takes_everything():
    assert "where (true)" in render(EMAIL, [], [EMAIL])


def test_unique_ids_with_quotes_are_escaped_as_literals():
    sql = render(SLACK, [entry("model.p.a'; drop table x; --", "chat")], [EMAIL, SLACK])
    assert "node_key = 'model.p.a\\'; drop table x; --'" in sql


CHANNEL_VARS = {"alert_channels": {
    "email": {"type": "email", "integration": "ICECORE_EMAIL_INT", "recipients": ["data@example.com"]},
    "chat": {"type": "slack", "integration": "ICECORE_SLACK_INT"}, "teams": {"type": "teams", "integration": "ICECORE_TEAMS_INT"}}}


def test_channels_are_validated_and_sorted():
    channels = call("alert_channels", {}, CHANNEL_VARS)
    assert [c["name"] for c in channels] == ["chat", "email", "teams"]
    assert [c["default"] for c in channels] == [False, False, False]
    assert call("alert_channels", {}, {"alert_channels": {"only": {"type": "slack", "integration": "I"}}})[0]["default"]


@pytest.mark.parametrize("bad", [
    {"x'; drop": {"type": "slack", "integration": "I"}},
    {"a": {"type": "pager", "integration": "I"}},
    {"a": {"type": "slack", "integration": "I'; drop"}},
    {"a": {"type": "slack"}},
    {"a": {"type": "email", "integration": "I"}},
    {"a": {"type": "email", "integration": "I", "recipients": ["x'@y.com"]}},
    {"a": "slack"},
])
def test_bad_channel_config_is_a_compile_error(bad):
    with pytest.raises(MacroFailed):
        call("alert_channels", {}, {"alert_channels": bad})


def test_nothing_is_configured_by_default():
    assert call("alert_channels", {}) == []


def test_integration_statements_are_printed_not_run():
    out = call("alert_integration_sql", {"channels": [EMAIL, SLACK, TEAMS], "schema": "my_obs"})
    text = "\n".join(out)
    assert "CREATE NOTIFICATION INTEGRATION ICECORE_EMAIL_INT TYPE = EMAIL ENABLED = TRUE ALLOWED_RECIPIENTS = ('data@example.com');" in text
    assert "WEBHOOK_URL = 'https://hooks.slack.com/services/SNOWFLAKE_WEBHOOK_SECRET'" in text
    assert "CREATE SECRET" in text and "SNOWFLAKE_WEBHOOK_MESSAGE" in text
    assert "GRANT USAGE ON INTEGRATION ICECORE_TEAMS_INT TO ROLE" in text and "GRANT EXECUTE ALERT ON ACCOUNT" in text


def test_setup_marks_create_alerts_ready():
    steps = {s["name"]: s["ready"] for s in call("_icecore_setup_steps", {})}
    assert steps["create_alerts"] is True and steps["create_tasks"] is False


def test_schedule_is_a_var():
    assert "SCHEDULE = '15 MINUTE'" in call("render_alert_sql", {
        "channel": EMAIL, "entries": [], "event_table": EVENT_TABLE, "warehouse": "WH", "all_channels": [EMAIL]},
        {"alert_schedule_minutes": 15})


# --- sandbox --------------------------------------------------------------------------------------

HOSTILE = "q'\\'$$;\nz"


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    run_id = uuid.uuid4().hex
    canary = f'"{DATABASE}"."SPIKES"."it_canary_{run_id}"'
    with obs_schema(sf) as schema:
        settings = {**icecore_vars(schema)["icecore"], **CHANNEL_VARS}
        obs_vars = ["--vars", json.dumps({"icecore": settings})]
        since = snowflake_since(sf)
        evil_rel = f"\"X\".\"Y\".\"{HOSTILE}'; create table {canary} (a int); --\""
        run_macro("it_emit_events", {"events": [{"kind": "schema_change", "attributes": {
            "dbt.node.relation": evil_rel, "dbt.schema.change_kind": "added",
            "dbt.schema.column": HOSTILE, "dbt.schema.to_type": "NUMBER"}}]}, obs_vars)
        test_id = f"test.p.{HOSTILE}_{run_id}"
        run_macro("it_emit_run_items", {"items": [{"type": "log", "level": "error", "message": HOSTILE, "attrs": {
            "icecore.kind": "node_result", "dbt.node.unique_id": test_id, "dbt.node.type": "test",
            "dbt.test.name": HOSTILE, "dbt.result.status": "fail", "dbt.result.message": HOSTILE,
            "dbt.root_cause.unique_id": "model.p.upstream", "dbt.root_cause.kind": "stale"}}]}, obs_vars)
        wait_for_events(sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                            " and (record:name::string = 'icecore.schema_change' or record_type = 'LOG')",
                        {"schema": schema}, min_rows=2, since=since, settle_s=10)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        alerts = run_macro("it_create_alerts", {"schema": schema}, obs_vars)
        try:
            yield {"schema": schema, "obs_vars": obs_vars, "settings": settings, "alerts": alerts, "canary": canary,
                   "since": since, "test_id": test_id}
        finally:
            sf.cursor().execute(f"drop table if exists {canary}")


def _rows(cur, sql):
    cur.execute(sql)
    return [{k.lower(): v for k, v in zip([c[0] for c in cur.description], r)} for r in cur.fetchall()]


def _condition(rendered: str) -> str:
    return rendered.split("IF (EXISTS (\n", 1)[1].split("\n  ))\n  THEN", 1)[0]


def _alert_sql(seeded, channel, all_channels=None) -> str:
    return call("render_alert_sql", {"channel": channel, "entries": [], "schema": seeded["schema"],
                                     "event_table": f"{DATABASE}.TELEMETRY.EVENTS",
                                     "all_channels": all_channels or [EMAIL, SLACK, TEAMS]}, seeded["settings"])


@pytest.mark.sandbox
def test_alerts_are_created_suspended_one_per_channel(sf, seeded):
    assert [(a["channel"], a["status"], a["resumed"]) for a in seeded["alerts"]] == [
        ("chat", "ok", False), ("email", "ok", False), ("teams", "ok", False)]
    rows = _rows(sf.cursor(), f'show alerts in schema "{DATABASE}"."{seeded["schema"]}"')
    assert sorted(r["name"] for r in rows) == ["ICECORE_ALERT_CHAT", "ICECORE_ALERT_EMAIL", "ICECORE_ALERT_TEAMS"]
    assert {r["state"] for r in rows} == {"suspended"}


SOLO = {"alert_channels": {"solo": {"type": "email", "integration": "ICECORE_NO_SUCH_INTEGRATION",
                                    "recipients": ["data@example.com"]}}}


@pytest.mark.sandbox
def test_execute_alert_runs_condition_and_action_up_to_the_send(sf, seeded):
    """EXECUTE ALERT on a suspended alert (the owner granted EXECUTE ALERT to the CI role). The sandbox has no
    notification integration, so the send step errors; the action must have run up to it, and because nothing was sent
    nothing is recorded as notified: the incidents stay pending. The alert is dropped and never resumed."""
    schema = seeded["schema"]
    settings = {**seeded["settings"], **SOLO}
    obs_vars = ["--vars", json.dumps({"icecore": settings})]
    created = run_macro("it_create_alerts", {"schema": schema}, obs_vars)
    assert [(a["channel"], a["status"], a["resumed"]) for a in created] == [("solo", "ok", False)]
    alert = f'"{DATABASE}"."{schema}"."ICECORE_ALERT_SOLO"'
    cur = sf.cursor()
    try:
        assert _rows(cur, f"show alerts like 'ICECORE_ALERT_SOLO' in schema \"{DATABASE}\".\"{schema}\"")[0]["state"] == "suspended"
        since = snowflake_since(sf, 1)
        cur.execute(f"execute alert {alert}")
        history, deadline = [], time.monotonic() + 240
        while time.monotonic() < deadline:
            history = _rows(sf.cursor(), (
                f'select state, sql_error_code, sql_error_message from table("{DATABASE}".information_schema.alert_history('
                f"scheduled_time_range_start => dateadd(hour, -1, current_timestamp()), result_limit => 20, "
                f"alert_name => 'ICECORE_ALERT_SOLO')) where schema_name = '{schema}' and state not in ('SCHEDULED', 'EXECUTING')"))
            if history:
                break
            time.sleep(10)
        assert history, "the alert run never reached ALERT_HISTORY"
        run = history[0]
        # the condition held (two incidents) so the action ran; it stopped at the send: no such integration
        assert run["state"] == "ACTION_FAILED", run
        assert "ICECORE_NO_SUCH_INTEGRATION" in (run["sql_error_message"] or ""), run
        assert "not a valid notification integration" in run["sql_error_message"], run
        # nothing was sent, so nothing was recorded: no notified record, the incidents are still pending
        assert_no_events(
            sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.notified'",
            {"schema": schema}, wait_s=30, since=since)
        solo = {"name": "solo", "type": "email", "integration": "ICECORE_NO_SUCH_INTEGRATION",
                "recipients": ["data@example.com"], "default": True}
        assert len(_rows(sf.cursor(), _condition(_alert_sql(seeded, solo, [solo])))) == 2
        assert _rows(sf.cursor(), f"show alerts like 'ICECORE_ALERT_SOLO' in schema \"{DATABASE}\".\"{schema}\"")[0]["state"] == "suspended"
    finally:
        cur.execute(f"drop alert if exists {alert}")


@pytest.mark.sandbox
def test_condition_returns_the_new_incidents_and_hostile_names_run_nothing_extra(sf, seeded):
    cur = sf.cursor()
    rows = _rows(cur, _condition(_alert_sql(seeded, EMAIL)))
    assert sorted(r["kind"] for r in rows) == ["schema_change", "test_failure"]
    assert sf.cursor().execute(f"show tables like 'it_canary_{seeded['canary'].split('_')[-1][:-1]}' in schema "
                               f'"{DATABASE}"."SPIKES"').fetchall() == []
    failure = next(r for r in rows if r["kind"] == "test_failure")
    assert failure["root_cause_unique_id"] == "model.p.upstream" and failure["root_cause_kind"] == "stale"
    assert failure["node_key"] == seeded["test_id"]


@pytest.mark.sandbox
@pytest.mark.parametrize("channel", [EMAIL, SLACK, TEAMS])
def test_message_names_node_kind_and_root_cause_and_keeps_hostile_text_as_data(sf, seeded, channel):
    condition = _condition(_alert_sql(seeded, {**channel, "default": True}, [{**channel, "default": True}]))  # the only channel is the default: it gets everything
    sql = call("alert_message_sql", {"type": channel["type"], "source": f"(\n{condition}\n)"})
    (message,) = sf.cursor().execute(sql).fetchone()
    assert "test_failure" in message and "schema_change" in message
    assert seeded["test_id"] in message and "model.p.upstream" in message and "stale" in message
    assert HOSTILE in message and message.startswith("icecore: 2 new incident(s)")
    assert sf.cursor().execute(f"show tables like 'it_canary%' in schema \"{DATABASE}\".\"SPIKES\"").fetchall() == []


@pytest.mark.sandbox
def test_action_records_notified_events_and_the_incidents_are_then_quiet(sf, seeded):
    cur = sf.cursor()
    condition = _condition(_alert_sql(seeded, EMAIL))
    cur.execute(condition)
    qid = cur.sfqid
    block = call("_icecore_alert_action_sql", {"channel": EMAIL, "schema": seeded["schema"], "send": False,
                                               "qid": f"'{qid}'"}, seeded["settings"])
    assert "SEND_SNOWFLAKE_NOTIFICATION" not in block and "$$" not in block
    since = snowflake_since(sf, 1)
    sf.cursor().execute(f"execute immediate $$\n{block}\n$$")
    rows = wait_for_events(
        sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.notified'",
        {"schema": seeded["schema"]}, min_rows=2, since=since, settle_s=5)
    assert {r["record_attributes"]["icecore.notified.channel"] for r in rows} == {"email"}
    assert all(r["resource_attributes"]["snow.executable.name"].startswith("ICECORE_NOTIFIED(") for r in rows)
    assert len(rows) == 2
    assert _rows(sf.cursor(), _condition(_alert_sql(seeded, EMAIL))) == []          # notified: quiet inside the window
    assert len(_rows(sf.cursor(), _condition(_alert_sql(seeded, SLACK)))) == 0       # chat claims nothing
    assert sf.cursor().execute(f'show alerts in schema "{DATABASE}"."{seeded["schema"]}"').fetchall()
    assert {r["state"] for r in _rows(sf.cursor(), f'show alerts in schema "{DATABASE}"."{seeded["schema"]}"')} == {"suspended"}
