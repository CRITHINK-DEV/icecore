"""UC6 silence detection: icecore.render_silence_task_sql and icecore.create_silence_task (macros/control/), and the
`silence_events` view (macros/views/silence_events.sql).

Offline tests render the SQL. The sandbox test creates the emitter procedures and the views in ICECORE_OBS_<run_id>
(the obs_schema var), stands in for node_executions with a view of two nodes, one whose last success was 90 minutes
ago (expected every 1h, grace 1 minute) and one that succeeded 10 minutes ago, creates the task (suspended), runs it
once with EXECUTE TASK, reads the verdict back from silence_events, runs it again to see no duplicate, and drops the
task and the schema. The 90 minutes cannot be written through the emitter (events are stamped now), hence the
stand-in view. Never leaves a task running: it is created suspended and dropped.
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector import DictCursor
from snowflake.connector.errors import ProgrammingError

from helpers.events import wait_for_events
from helpers.macros import MacroFailed, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
NOW = "convert_timezone('UTC', current_timestamp())::timestamp_ntz"


def entry(unique_id="model.p.a", every=60, grace=1, channel=None, severity="warn") -> dict:
    return {"unique_id": unique_id, "name": unique_id.split(".")[-1], "resource_type": "model",
            "expected_every_minutes": every, "grace_minutes": grace, "grace_defaulted": False,
            "channel": channel, "severity": severity}


def task_sql(entries, **kwargs) -> str:
    return run_macro("it_silence_task_sql", {"entries": entries, **kwargs})


# --- offline: rendering only, no connection ------------------------------------------------------

def test_sla_entries_are_embedded_with_their_grace():
    sql = task_sql([entry("model.p.a", 60, 15, channel="data-platform", severity="error"), entry("model.p.b", 1440, 1)])
    assert "('model.p.a', 60, 15, 'data-platform', 'error')" in sql
    assert "('model.p.b', 1440, 1, null, 'warn')" in sql


def test_the_rule_compares_snowflakes_clock_with_the_last_success_plus_period_plus_grace():
    sql = task_sql([entry()])
    assert "FROM \"" in sql and '."NODE_EXECUTIONS"' in sql
    assert "status IN ('success', 'pass')" in sql and "MAX(recorded_at)" in sql
    assert ("convert_timezone('UTC', current_timestamp())::timestamp_ntz > "
            "DATEADD(minute, sla.every_min + sla.grace_min, last_success.last_success_at)") in sql


def test_the_verdict_is_a_span_event_written_by_a_call_of_icecore_emit_never_an_insert():
    sql = task_sql([entry()])
    assert "'icecore.silence'" in sql and '."ICECORE_EMIT"(:events) INTO :res' in sql
    for word in ("INSERT", "MERGE", "CREATE TABLE"):
        assert word not in sql.upper(), word
    for key in ["icecore.silence.expected_at", "icecore.silence.last_success_at", "icecore.silence.grace_minutes",
                "icecore.silence.expected_every_minutes", "dbt.node.unique_id"]:
        assert f"'{key}'" in sql, key


def test_one_verdict_per_missed_run_and_at_most_100_per_call():
    sql = task_sql([entry()])
    assert '."SILENCE_EVENTS" s WHERE s.unique_id = due.unique_id AND s.expected_at = due.expected_at' in sql
    assert "LIMIT 100" in sql


def test_errors_return_a_message_instead_of_failing_the_task():
    assert "WHEN OTHER THEN RETURN 'icecore-error '" in task_sql([entry()])


def test_source_and_schema_arguments():
    assert "FROM X.Y.Z\n" in task_sql([entry()], source="X.Y.Z")
    sql = task_sql([entry()], schema="my_obs")
    assert '"MY_OBS"."NODE_EXECUTIONS"' in sql and '"MY_OBS"."ICECORE_EMIT"' in sql


def test_a_node_without_meta_sla_is_not_a_schedule():
    sql = task_sql([entry("model.p.a"), {**entry("model.p.alerts_only"), "expected_every_minutes": None,
                                         "grace_minutes": None}])
    assert "model.p.a" in sql and "alerts_only" not in sql


def test_no_schedule_at_all_is_refused():
    with pytest.raises(MacroFailed):
        task_sql([])


def test_quotes_in_a_unique_id_are_escaped():
    assert "'model.p.a\\'b'" in task_sql([entry("model.p.a'b")])


def test_the_task_is_created_in_the_obs_schema_with_a_schedule_and_a_warehouse():
    ddl = run_macro("it_silence_task_ddl", {"schema": "my_obs", "warehouse": "my wh", "schedule": "10 MINUTE"})
    assert ddl.startswith('CREATE OR REPLACE TASK "') and '"MY_OBS"."ICECORE_SILENCE"' in ddl
    assert 'WAREHOUSE = "my wh"' in ddl and "SCHEDULE = '10 MINUTE'" in ddl
    assert "EXECUTE IMMEDIATE $$" in ddl and "RESUME" not in ddl  # created suspended: resuming is a separate step


def test_without_meta_sla_no_task_is_created():
    out = run_macro("it_create_silence_task", {"entries": [{**entry(), "expected_every_minutes": None}]})
    assert out["status"] == "skipped" and out["task"] is None and out["printed"] == []


def test_setup_marks_create_tasks_ready_and_suspended_by_default():
    from helpers.macros import call_macro
    steps = {s["name"]: s for s in call_macro("_icecore_setup_steps")}
    assert steps["create_tasks"]["ready"] is True
    assert "silence_task_resume: false" in open(
        os.path.join(os.path.dirname(__file__), "..", "..", "dbt_project.yml")).read()


def test_silence_events_view_is_in_the_create_views_list_and_reads_the_verdict_keys():
    views = {v["name"]: v["sql"] for v in run_macro("it_views_render", {})}
    sql = views["SILENCE_EVENTS"]
    assert "record_name = 'icecore.silence'" in sql and "event_at as detected_at" in sql
    for key in ["expected_at", "last_success_at", "grace_minutes", "expected_every_minutes"]:
        assert f'"icecore.silence.{key}"' in sql, key


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def silence(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    qs = f'"{DATABASE}"."{schema}"'
    ids = {"late": f"model.it.late_{run_id}", "ok": f"model.it.ok_{run_id}", "failed": f"model.it.failed_{run_id}"}
    cur = sf.cursor()
    cur.execute(f"create schema {qs}")
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        # fixed instants, taken once on Snowflake's clock: a view over "now - 90 minutes" would move with every query
        t = {m: cur.execute(f"select dateadd(minute, -{m}, {NOW})::string").fetchone()[0] for m in (5, 10, 90, 200)}
        cur.execute(f"""create view {qs}."STANDIN_NODES" as
            select unique_id, status, recorded_at::timestamp_ntz as recorded_at from values
              ('{ids["late"]}', 'success', '{t[90]}'),
              ('{ids["late"]}', 'error', '{t[5]}'),
              ('{ids["ok"]}', 'success', '{t[10]}'),
              ('{ids["failed"]}', 'error', '{t[200]}')
            as t(unique_id, status, recorded_at)""")
        entries = [entry(ids["late"], 60, 1, channel="data-platform", severity="error"), entry(ids["ok"], 60, 1),
                   entry(ids["failed"], 60, 1)]
        since = cur.execute(f"select dateadd(minute, -2, {NOW})").fetchone()[0]
        created = run_macro("it_create_silence_task",
                            {"schema": schema, "entries": entries, "source": f'{qs}."STANDIN_NODES"'}, obs_vars)
        yield {"schema": schema, "qs": qs, "ids": ids, "created": created, "since": since, "cur": cur, "sf": sf,
               "entries": entries, "source": f'{qs}."STANDIN_NODES"', "obs_vars": obs_vars}
    finally:
        try:
            cur.execute(f'alter task if exists {qs}."ICECORE_SILENCE" suspend')
            cur.execute(f'drop task if exists {qs}."ICECORE_SILENCE"')
        finally:
            cur.execute(f"drop schema if exists {qs} cascade")


def run_task_once(env) -> str:
    """EXECUTE TASK, the way the story asks. When the role lacks the EXECUTE TASK privilege on the account (error 091089),
    run the task's own body with EXECUTE IMMEDIATE instead, so the logic is still proved; returns "task" or "body"."""
    try:
        env["cur"].execute(f'execute task {env["qs"]}."ICECORE_SILENCE"')
        return "task"
    except ProgrammingError as e:
        if e.errno != 91089:
            raise
        body = run_macro("it_silence_task_sql", {"entries": env["entries"], "schema": env["schema"],
                                                 "source": env["source"]}, env["obs_vars"])
        env["cur"].execute("execute immediate $$\n" + body + "\n$$")
        return "body"


def _records(env, unique_id: str, min_rows: int = 1, settle_s: float = 0) -> list:
    return wait_for_events(
        env["sf"], "record:name::string = 'icecore.silence' and record_attributes:\"dbt.node.unique_id\"::string = %(u)s"
                   " and resource_attributes:\"snow.schema.name\"::string = %(s)s",
        {"u": unique_id, "s": env["schema"]}, min_rows=min_rows, since=env["since"], settle_s=settle_s, timeout_s=240)


@pytest.mark.sandbox
def test_the_task_is_created_suspended(silence):
    assert silence["created"]["status"] == "ok" and silence["created"]["resumed"] is False
    (task,) = silence["sf"].cursor(DictCursor).execute(
        f'show tasks like \'ICECORE_SILENCE\' in schema {silence["qs"]}').fetchall()
    assert task["state"] == "suspended" and task["schedule"] == "5 MINUTE"


@pytest.mark.sandbox
def test_no_run_for_90_minutes_lands_one_silence_record_and_a_run_within_the_sla_lands_none(silence):
    run_task_once(silence)
    (record,) = _records(silence, silence["ids"]["late"], settle_s=15)
    a = record["record_attributes"]
    assert a["icecore.silence.grace_minutes"] == 1 and a["icecore.silence.expected_every_minutes"] == 60
    assert a["icecore.silence.channel"] == "data-platform" and a["icecore.silence.severity"] == "error"
    assert a["icecore.level"] == "warn"
    # the other two nodes: nothing landed in the same window (the first CALL's records have settled)
    for key in ("ok", "failed"):
        rows = silence["sf"].cursor().execute(
            "select count(*) from " + os.environ.get("ICECORE_EVENT_TABLE", "ICECORE_SANDBOX.TELEMETRY.EVENTS")
            + " where record:name::string = 'icecore.silence' and record_attributes:\"dbt.node.unique_id\"::string = %(u)s"
            " and timestamp >= %(since)s", {"u": silence["ids"][key], "since": silence["since"]}).fetchone()[0]
        assert rows == 0, key


@pytest.mark.sandbox
def test_silence_events_has_expected_at_and_last_success_at_filled(silence):
    _records(silence, silence["ids"]["late"])
    deadline = time.monotonic() + 120
    while True:
        rows = silence["sf"].cursor(DictCursor).execute(
            f'select * from {silence["qs"]}."SILENCE_EVENTS" where unique_id = %(u)s',
            {"u": silence["ids"]["late"]}).fetchall()
        if rows or time.monotonic() > deadline:
            break
        time.sleep(5)
    (row,) = [{k.lower(): v for k, v in r.items()} for r in rows]
    assert row["expected_at"] and row["last_success_at"] and row["detected_at"]
    assert (row["grace"], row["expected_every_minutes"]) == (1, 60)
    # expected_at is last success + one period, both on Snowflake's clock
    assert (row["expected_at"] - row["last_success_at"]).total_seconds() == 3600


@pytest.mark.sandbox
def test_a_second_run_does_not_repeat_the_verdict_once_it_is_readable(silence):
    _records(silence, silence["ids"]["late"])
    deadline = time.monotonic() + 120
    while not silence["cur"].execute(
            f'select count(*) from {silence["qs"]}."SILENCE_EVENTS" where unique_id = %(u)s',
            {"u": silence["ids"]["late"]}).fetchone()[0] and time.monotonic() < deadline:
        time.sleep(5)
    run_task_once(silence)
    time.sleep(45)
    assert len(_records(silence, silence["ids"]["late"], settle_s=10)) == 1


@pytest.mark.sandbox
def test_execute_task_runs_the_created_task(silence):
    """Needs the EXECUTE TASK privilege on the account (an admin grants it); skipped, with the reason, without."""
    try:
        silence["cur"].execute(f'execute task {silence["qs"]}."ICECORE_SILENCE"')
    except ProgrammingError as e:
        if e.errno == 91089:
            pytest.skip("role lacks EXECUTE TASK on the account: GRANT EXECUTE TASK ON ACCOUNT TO ROLE " + os.environ.get("SNOWFLAKE_ROLE", "<role>"))
        raise
