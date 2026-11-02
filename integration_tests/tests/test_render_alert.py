"""UC6 render_alert_sql and create_alerts (macros/control/render_alert_sql.sql): one ALERT per channel.

Offline tests render the SQL and the validation. The sandbox test creates the emitter procedures and the views in a
run_id-named schema, ICECORE_OBS_<run_id>; seeds incidents whose names hold quotes, backslashes, $$, ; and newlines (and
a statement that would create a canary table); creates the alerts (suspended); runs each alert's condition query and
the action with the notification CALL left out, because the sandbox has no notification integration: what could not
run is the SYSTEM$SEND_SNOWFLAKE_NOTIFICATION call itself. Alerts are never resumed; the schema is dropped at the end.
"""
import json
import os
import re
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
    kwargs.setdefault("nodes", {})
    return call("render_alert_sql", {"channel": channel, "entries": list(entries), "event_table": EVENT_TABLE,
                                     "warehouse": "WH", "all_channels": all_channels or [channel], **kwargs})


def entry(unique_id, channel, severity="warn"):
    return {"unique_id": unique_id, "name": unique_id.split(".")[-1], "resource_type": "model", "channel": channel,
            "severity": severity}


LITERAL = re.compile(r"PARSE_JSON\('((?:[^'\\]|\\.)*)'\)")


def constant_text(sql: str) -> str:
    """The alert text with its JSON config literals (PARSE_JSON of one string literal) taken out."""
    return LITERAL.sub("PARSE_JSON(<config>)", sql)


def config_literals(sql: str) -> list:
    """The config objects of the alert, in text order: condition routing, dedup channel, action config."""
    return [json.loads(m.encode().decode("unicode_escape")) for m in LITERAL.findall(sql)]


# --- offline: rendering only, no connection ------------------------------------------------------

def test_email_alert_condition_is_the_dedup_select_and_action_records_then_sends():
    sql = render(EMAIL, schema="my_obs")
    assert sql.startswith('CREATE OR REPLACE ALERT "' + DATABASE + '"."MY_OBS"."ICECORE_ALERT_EMAIL"')
    assert "WAREHOUSE = WH" in sql and "SCHEDULE = '5 MINUTE'" in sql and "IF (EXISTS (" in sql
    assert f'from "{DATABASE}"."MY_OBS"."ALERTS_FEED"' in sql and "and notified.channel = PARSE_JSON(" in sql
    assert "where feed.acknowledged_at is null" in sql
    assert "SNOWFLAKE.ALERT.GET_CONDITION_QUERY_UUID()" in sql and "result_scan(:qid)" in sql
    # the 3-argument form of Snowflake's EMAIL_INTEGRATION_CONFIG (integration, subject, to ARRAY), read from the config
    assert ("SNOWFLAKE.NOTIFICATION.EMAIL_INTEGRATION_CONFIG(:cfg:integration::STRING, 'icecore: new incidents', "
            ":cfg:recipients::ARRAY)") in sql
    assert "CALL SYSTEM$SEND_SNOWFLAKE_NOTIFICATION(SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(:msg)" in sql
    assert f'CALL "{DATABASE}"."MY_OBS"."ICECORE_NOTIFIED"(:k, :ch) INTO :st;' in sql
    assert "insert" not in sql.lower() and "$$" not in sql and "execute immediate" not in sql.lower()
    # recording comes first and only incidents recorded ok are sent; a failed recording is logged, not sent
    assert sql.index('"ICECORE_NOTIFIED"(:k') < sql.index("SEND_SNOWFLAKE_NOTIFICATION")
    assert "IF (STARTSWITH(st, 'ok')) THEN" in sql and "array_contains(incident_key::variant, :recorded)" in sql
    assert "'icecore.alert_record_failed'" in sql and "IF (ARRAY_SIZE(recorded) > 0) THEN" in sql
    assert "recorded := ARRAY_APPEND(recorded, :k);" in sql


def test_the_alert_text_is_constant_and_every_config_value_is_one_json_literal():
    """Node ids, channel, integration and recipients appear only inside PARSE_JSON string literals."""
    nasty = "model.p.a'; drop table x; --"
    two = {**EMAIL, "integration": "OTHER_INT", "recipients": ["z@example.com", "y@example.com"]}
    sql_one = render(EMAIL, [entry(nasty, "email")], [EMAIL, SLACK])
    sql_two = render(two, [entry("model.p.zzz", "email")], [two, SLACK])
    assert constant_text(sql_one) == constant_text(sql_two)
    for needle in ["data@example.com", "ICECORE_EMAIL_INT", "drop table", "model.p"]:
        assert needle not in constant_text(sql_one), needle
    condition_cfg, dedup_cfg, action_cfg = config_literals(sql_one)
    assert condition_cfg["channel"] == "email" and nasty in condition_cfg["any"] and nasty in condition_cfg["claimed"]
    assert dedup_cfg == {"channel": "email"}
    assert action_cfg == {"channel": "email", "integration": "ICECORE_EMAIL_INT", "recipients": ["data@example.com"]}


@pytest.mark.parametrize("channel, integration", [(SLACK, "ICECORE_SLACK_INT"), (TEAMS, "ICECORE_TEAMS_INT")])
def test_webhook_alerts_sanitize_the_text_and_use_the_integration(channel, integration):
    sql = render(channel)
    assert "SNOWFLAKE.NOTIFICATION.INTEGRATION(:cfg:integration::STRING)" in sql
    assert config_literals(sql)[-1]["integration"] == integration
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


def test_slack_text_escapes_ampersand_and_angle_brackets_and_every_webhook_cleans_quotes():
    slack = call("alert_message_sql", {"type": "slack", "source": "pending"})
    assert "replace(replace(replace(coalesce(title, ''), '&', '&amp;'), '<', '&lt;'), '>', '&gt;')" in slack
    for webhook in ("slack", "teams"):
        sql = call("alert_message_sql", {"type": webhook, "source": "pending"})
        assert "'[[:cntrl:]]'" in sql and "''''" in sql and "'\\\\'" in sql
        assert "'\\n'" not in sql  # one line: no raw newline into a JSON body template
    email = call("alert_message_sql", {"type": "email", "source": "pending"})
    assert "&amp;" not in email and "cntrl" not in email and "'\\n'" in email


def test_the_action_reads_the_incidents_of_the_condition_query_and_loops_over_the_first_50():
    sql = render(EMAIL)
    assert "LET rs RESULTSET := (select incident_key from table(result_scan(:qid)) order by first_detected_at, incident_key limit 50);" in sql
    assert "LET c CURSOR FOR rs;" in sql and "FOR r IN c DO" in sql and "k := r.incident_key;" in sql


def test_routing_sends_a_claimed_node_to_its_channel_and_the_rest_to_the_default():
    entries = [entry("model.p.a", "chat"), entry("model.p.b", "chat", "error"), entry("model.p.c", "email")]
    both = [EMAIL, SLACK]
    chat = config_literals(render(SLACK, entries, both))[0]
    assert chat == {"channel": "chat", "default": False, "any": ["model.p.a"], "error": ["model.p.b"],
                    "claimed": ["model.p.a", "model.p.b", "model.p.c"]}
    email = config_literals(render(EMAIL, entries, both))[0]
    assert email["default"] is True and email["any"] == ["model.p.c"] and email["error"] == []
    sql = render(EMAIL, entries, both)
    assert "cfg.c:default::boolean and not (" in sql and "pending.severity = 'error' and (" in sql


def test_a_channel_nobody_claims_and_that_is_not_default_routes_nothing():
    cfg = config_literals(render(SLACK, [], [EMAIL, SLACK]))[0]
    assert cfg["default"] is False and cfg["any"] == [] and cfg["error"] == []


def test_default_channel_with_nothing_claimed_takes_everything():
    cfg = config_literals(render(EMAIL, [], [EMAIL]))[0]
    assert cfg["default"] is True and cfg["claimed"] == []


MODEL = {"unique_id": "model.p.orders", "resource_type": "model", "name": "orders", "database": "DB", "schema": "S",
         "alias": "orders", "config": {}}
NODES = {
    "model.p.orders": MODEL,
    "test.p.not_null_orders_id.1": {"unique_id": "test.p.not_null_orders_id.1", "resource_type": "test",
                                    "attached_node": "model.p.orders", "depends_on": {"nodes": ["model.p.orders"]}},
    "test.p.rel_customers_orders.2": {"unique_id": "test.p.rel_customers_orders.2", "resource_type": "test",
                                      "depends_on": {"nodes": ["model.p.customers", "model.p.orders"]}},
    "test.p.other.3": {"unique_id": "test.p.other.3", "resource_type": "test", "attached_node": "model.p.other",
                       "depends_on": {"nodes": ["model.p.other"]}},
}


def test_a_models_alerts_route_its_relation_and_its_tests():
    sql = render(SLACK, [entry("model.p.orders", "chat")], [EMAIL, SLACK], nodes=NODES)
    cfg = config_literals(sql)[0]
    # the model, its relation (as anomalies and schema changes carry it) and its attached test; the multi-parent test
    # without attached_node goes to its first model dependency (customers), not to orders
    assert cfg["any"] == ["model.p.orders", '"DB"."S"."ORDERS"', "test.p.not_null_orders_id.1"]
    assert "array_contains(split_part(pending.node_key, '|', 1)::variant, cfg.c:any::array)" in sql


def test_an_unknown_meta_alerts_channel_is_a_compile_error_naming_the_node():
    with pytest.raises(MacroFailed) as err:
        render(EMAIL, [entry("model.p.orders", "pager")], [EMAIL, SLACK])
    assert "orders" in str(err.value) and "pager" in str(err.value) and "not a configured alert channel" in str(err.value)
    with pytest.raises(MacroFailed):
        render(EMAIL, [entry("model.p.orders", "data-platform")], [EMAIL])  # not a valid channel name either


CHANNEL_VARS = {"alert_channels": {
    "email": {"type": "email", "integration": "ICECORE_EMAIL_INT", "recipients": ["data@example.com"], "default": True},
    "chat": {"type": "slack", "integration": "ICECORE_SLACK_INT"}, "teams": {"type": "teams", "integration": "ICECORE_TEAMS_INT"}}}


def test_channels_are_validated_and_sorted():
    channels = call("alert_channels", {}, CHANNEL_VARS)
    assert [c["name"] for c in channels] == ["chat", "email", "teams"]
    assert [c["default"] for c in channels] == [False, True, False]
    assert call("alert_channels", {}, {"alert_channels": {"only": {"type": "slack", "integration": "I"}}})[0]["default"]


@pytest.mark.parametrize("defaults", [(), ("a", "b")])
def test_two_or_more_channels_need_exactly_one_default(defaults):
    channels = {n: {"type": "slack", "integration": "I", **({"default": True} if n in defaults else {})} for n in "ab"}
    with pytest.raises(MacroFailed) as err:
        call("alert_channels", {}, {"alert_channels": channels})
    assert "exactly one must have default: true" in str(err.value)


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
    assert text.count("ALTER ALERT") == 3 and "RESUME;" in text and "icecore never resumes an alert" in text


def test_create_alerts_has_no_resume_path():
    source = open(os.path.join(os.path.dirname(__file__), "..", "..", "macros", "control", "render_alert_sql.sql")).read()
    assert "alert_resume" not in source
    assert not re.search(r"guarded_ddl\([^)]*RESUME", source)


def test_setup_marks_create_alerts_ready():
    steps = {s["name"]: s["ready"] for s in call("_icecore_setup_steps", {})}
    assert steps["create_alerts"] is True and steps["create_tasks"] is True


def test_schedule_is_a_var():
    assert "SCHEDULE = '15 MINUTE'" in call("render_alert_sql", {
        "channel": EMAIL, "entries": [], "event_table": EVENT_TABLE, "warehouse": "WH", "all_channels": [EMAIL],
        "nodes": {}},
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
    return call("render_alert_sql", {"channel": channel, "entries": [], "schema": seeded["schema"], "nodes": {},
                                     "event_table": f"{DATABASE}.TELEMETRY.EVENTS",
                                     "all_channels": all_channels or [EMAIL, SLACK, TEAMS]}, seeded["settings"])


@pytest.mark.sandbox
def test_alerts_are_created_suspended_one_per_channel(sf, seeded):
    assert [(a["channel"], a["status"]) for a in seeded["alerts"]] == [
        ("chat", "ok"), ("email", "ok"), ("teams", "ok")]
    rows = _rows(sf.cursor(), f'show alerts in schema "{DATABASE}"."{seeded["schema"]}"')
    assert sorted(r["name"] for r in rows) == ["ICECORE_ALERT_CHAT", "ICECORE_ALERT_EMAIL", "ICECORE_ALERT_TEAMS"]
    assert {r["state"] for r in rows} == {"suspended"}


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
    shown_id = seeded["test_id"] if channel["type"] == "email" else seeded["test_id"].replace("\\", "/").replace("\n", " ")
    assert shown_id in message and "model.p.upstream" in message and "stale" in message
    assert message.startswith("icecore: 2 new incident(s)")
    if channel["type"] == "email":
        assert HOSTILE in message  # plain text: the hostile text arrives as it is, as data
    else:
        # webhook text is cleaned: quote kept as text, backslash and newline replaced, no control character, one line
        assert "q'/'$$; z" in message and "\\" not in message and "\n" not in message and '"' not in message
    assert sf.cursor().execute(f"show tables like 'it_canary%' in schema \"{DATABASE}\".\"SPIKES\"").fetchall() == []


@pytest.mark.sandbox
def test_webhook_text_cleaning_and_what_sanitize_webhook_content_does_not_do(sf):
    """SANITIZE_WEBHOOK_CONTENT redacts the secret token only; it escapes no quote, backslash or angle bracket, so the
    message cleans them itself. How Snowflake substitutes SNOWFLAKE_WEBHOOK_MESSAGE into a webhook body template is NOT
    proven here (the sandbox has no webhook integration): the message therefore carries no character that could break a
    JSON string, whichever way it is substituted."""
    raw = "a " + chr(34) + "q" + chr(34) + " it" + chr(39) + "s b" + chr(92) + " c" + chr(10) + "<!channel> & d"
    (sanitized,) = sf.cursor().execute("select SNOWFLAKE.NOTIFICATION.SANITIZE_WEBHOOK_CONTENT(%s)", (raw,)).fetchone()
    assert sanitized == raw  # untouched
    (as_plain,) = sf.cursor().execute("select SNOWFLAKE.NOTIFICATION.TEXT_PLAIN(%s)", (raw,)).fetchone()
    assert json.loads(as_plain)["text/plain"] == raw  # TEXT_PLAIN JSON-escapes its own output, not the template substitution
    source = ("(select 'schema_change' as kind, 'a ' || chr(34) || 'q' || chr(34) || ' it' || chr(39) || 's b' || chr(92)"
              " || ' c' || chr(10) || '<!channel> & d' as title, 'n' as node_key, null as root_cause_unique_id,"
              " null as root_cause_kind, current_timestamp() as first_detected_at, 'k' as incident_key)")
    for type, expected in [("slack", "a 'q' it's b/ c &lt;!channel&gt; &amp; d"), ("teams", "a 'q' it's b/ c <!channel> & d")]:
        (message,) = sf.cursor().execute(call("alert_message_sql", {"type": type, "source": source})).fetchone()
        assert expected in message, message
        assert not any(ch in message for ch in ['"', "\\", "\n", "\r", "\t"]), message
    (email,) = sf.cursor().execute(call("alert_message_sql", {"type": "email", "source": source})).fetchone()
    assert raw in email  # plain text is not cleaned


@pytest.mark.sandbox
def test_a_failed_recording_is_logged_and_nothing_is_sent(sf, seeded):
    """ICECORE_NOTIFIED refuses a channel that is not a plain name (a returned status, not an exception). The action
    must not send (the send CALL here would fail on the missing integration, so completing proves it was skipped),
    and logs one icecore.alert_record_failed event per incident through ICECORE_EMIT."""
    bad = {**EMAIL, "name": "bad channel!", "default": True}
    cur = sf.cursor()
    cur.execute(_condition(_alert_sql(seeded, EMAIL)))
    qid = cur.sfqid
    block = call("_icecore_alert_action_sql", {"channel": bad, "schema": seeded["schema"], "send": True,
                                               "qid": f"'{qid}'"}, seeded["settings"])
    since = snowflake_since(sf, 1)
    sf.cursor().execute(f"execute immediate $$\n{block}\n$$")
    rows = wait_for_events(
        sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.alert_record_failed'",
        {"schema": seeded["schema"]}, min_rows=2, since=since, settle_s=5)
    assert len(rows) == 2 and all("not a plain name" in r["record_attributes"]["icecore.alert.status"] or
                                  "icecore-error" in r["record_attributes"]["icecore.alert.status"] for r in rows)
    assert_no_events(
        sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.notified'",
        {"schema": seeded["schema"]}, wait_s=20, since=since)


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


SOLO = {"alert_channels": {  # the fixture models of the test project route to email and chat: they must exist
    "solo": {"type": "email", "integration": "ICECORE_NO_SUCH_INTEGRATION", "recipients": ["data@example.com"], "default": True},
    "email": {"type": "email", "integration": "ICECORE_EMAIL_INT", "recipients": ["data@example.com"]},
    "chat": {"type": "slack", "integration": "ICECORE_SLACK_INT"}}}


@pytest.mark.sandbox
def test_zz_execute_alert_runs_condition_and_action_and_alert_history_shows_the_send_failing(sf, seeded):
    """EXECUTE ALERT on a suspended alert (the owner granted EXECUTE ALERT to the CI role): created suspended, executed
    once, dropped, never resumed. Last in the module because the action records the incidents. The sandbox has no
    notification integration, so the send errors after the incidents were recorded (record first, send last: at most
    once; an unsent incident is not repeated every run). ALERT_HISTORY shows ACTION_FAILED with that error."""
    schema = seeded["schema"]
    settings = {**seeded["settings"], **SOLO}
    obs_vars = ["--vars", json.dumps({"icecore": settings})]
    created = run_macro("it_create_alerts", {"schema": schema}, obs_vars)
    assert [(a["channel"], a["status"]) for a in created] == [("chat", "ok"), ("email", "ok"), ("solo", "ok")]
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
        # the action recorded the two incidents before sending, through ICECORE_NOTIFIED, as the alert's owner
        rows = wait_for_events(
            sf, "resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.notified'",
            {"schema": schema}, min_rows=2, since=since, settle_s=5)
        assert sum(r["record_attributes"]["icecore.notified.channel"] == "solo" for r in rows) == 2
        solo = {"name": "solo", "type": "email", "integration": "ICECORE_NO_SUCH_INTEGRATION",
                "recipients": ["data@example.com"], "default": True}
        assert _rows(sf.cursor(), _condition(_alert_sql(seeded, solo, [solo]))) == []  # recorded: quiet, not re-sent
        assert _rows(sf.cursor(), f"show alerts like 'ICECORE_ALERT_SOLO' in schema \"{DATABASE}\".\"{schema}\"")[0]["state"] == "suspended"
    finally:
        for name in ("SOLO", "EMAIL", "CHAT"):
            cur.execute(f'drop alert if exists "{DATABASE}"."{schema}"."ICECORE_ALERT_{name}"')
