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

EVIL = "x$$; GRANT ROLE ACCOUNTADMIN TO ROLE PUBLIC; SELECT $$ 'q' \\ \"d\" \n line2 ;--"


def literals(sql: str):
    """Split Snowflake SQL into (text outside single-quoted literals, [decoded literals]); backslash and doubled
    quote are escapes inside a literal, as Snowflake reads them."""
    outside, found, i = [], [], 0
    while i < len(sql):
        if sql[i] != "'":
            outside.append(sql[i])
            i += 1
            continue
        i += 1
        value = []
        while True:
            assert i < len(sql), "unterminated literal"
            c = sql[i]
            if c == "\\":
                value.append(sql[i + 1])
                i += 2
            elif c == "'" and sql[i + 1:i + 2] == "'":
                value.append("'")
                i += 2
            elif c == "'":
                i += 1
                break
            else:
                value.append(c)
                i += 1
        found.append("".join(value))
        outside.append("<lit>")
    return "".join(outside), found


def test_sla_entries_travel_as_one_json_literal_that_round_trips_unchanged():
    entries = [entry(EVIL, 60, 15, channel=EVIL, severity="error"), entry("model.p.b", 1440, 1)]
    body = task_sql(entries)
    outside, found = literals(body)
    assert outside.startswith("CALL ") and outside.endswith(")")
    sla = json.loads(found[0])
    assert sla == [{"unique_id": EVIL, "every_min": 60, "grace_min": 15, "channel": EVIL, "severity": "error"},
                   {"unique_id": "model.p.b", "every_min": 1440, "grace_min": 1, "channel": None, "severity": "warn"}]


@pytest.mark.parametrize("value", ["a$$b", "a'b", "a\\b", "a;b", "a\nb", "$$; DROP TABLE x; $$", "'; select 1; --", "\\'"])
def test_hostile_text_stays_data_and_adds_no_statement(value):
    body = task_sql([entry(value, channel=value)])
    ddl = run_macro("it_silence_task_ddl", {"body": body})
    for text in (body, ddl):
        outside, found = literals(text)
        assert ";" not in outside and "$$" not in outside, outside
        assert "<lit>" in outside
    assert json.loads(literals(body)[1][0])[0]["unique_id"] == value
    assert json.loads(literals(body)[1][0])[0]["channel"] == value


def test_the_rendered_create_task_is_one_statement_whose_text_does_not_depend_on_the_sla():
    plain = run_macro("it_silence_task_ddl", {"body": task_sql([entry("model.p.a")])})
    hostile = run_macro("it_silence_task_ddl", {"body": task_sql([entry(EVIL, channel=EVIL)])})
    assert literals(plain)[0] == literals(hostile)[0]
    assert plain.startswith("CREATE OR REPLACE TASK ") and plain.count("CREATE ") == 1


def test_the_procedure_is_static_and_reads_names_through_identifier():
    ddl = run_macro("it_silence_procedure_ddl", {})
    assert ddl == run_macro("it_silence_procedure_ddl", {})
    assert "IDENTIFIER(:source)" in ddl and "IDENTIFIER(:silence)" in ddl and "CALL IDENTIFIER(:emit)(:events)" in ddl
    assert "FLATTEN(INPUT => :sla)" in ddl
    assert "model." not in ddl and "meta" not in ddl.lower().replace("comment", "")


def test_the_rule_compares_snowflakes_clock_with_the_last_success_plus_period_plus_grace():
    ddl = run_macro("it_silence_procedure_ddl", {})
    assert "status IN ('success', 'pass')" in ddl and "MAX(recorded_at)" in ddl
    assert ("convert_timezone('UTC', current_timestamp())::timestamp_ntz > "
            "DATEADD(minute, s.every_min + s.grace_min, l.last_success_at)") in ddl


def test_the_verdict_is_a_span_event_written_by_a_call_of_icecore_emit_never_an_insert():
    ddl = run_macro("it_silence_procedure_ddl", {})
    assert "'icecore.silence'" in ddl
    for word in ("INSERT", "MERGE", "CREATE TABLE"):
        assert word not in ddl.upper().split("AS $$")[1], word
    for key in ["icecore.silence.expected_at", "icecore.silence.last_success_at", "icecore.silence.grace_minutes",
                "icecore.silence.expected_every_minutes", "dbt.node.unique_id"]:
        assert f"'{key}'" in ddl, key


def test_one_verdict_per_missed_run_and_at_most_100_per_call():
    ddl = run_macro("it_silence_procedure_ddl", {})
    assert "v.unique_id = due.unique_id AND v.expected_at = due.expected_at" in ddl and "LIMIT 100" in ddl


def test_a_failure_raises_so_the_task_history_shows_it():
    ddl = run_macro("it_silence_procedure_ddl", {})
    assert "RAISE emit_failed" in ddl and "STARTSWITH(res, 'icecore-error')" in ddl
    assert "EXCEPTION\n  WHEN OTHER" not in ddl and "RETURN 'icecore-error" not in ddl


def test_source_and_schema_arguments():
    _, found = literals(task_sql([entry()], source="X.Y.Z"))
    assert found[1] == "X.Y.Z"
    _, found = literals(task_sql([entry()], schema="my_obs"))
    assert found[1:] == ['"' + os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper() + '"."MY_OBS"."NODE_EXECUTIONS"',
                         '"' + os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper() + '"."MY_OBS"."SILENCE_EVENTS"',
                         '"' + os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper() + '"."MY_OBS"."ICECORE_EMIT"']


def test_a_node_without_meta_sla_is_not_a_schedule():
    sql = task_sql([entry("model.p.a"), {**entry("model.p.alerts_only"), "expected_every_minutes": None,
                                         "grace_minutes": None}])
    ids = [e["unique_id"] for e in json.loads(literals(sql)[1][0])]
    assert ids == ["model.p.a"]


def test_no_schedule_at_all_is_refused():
    with pytest.raises(MacroFailed):
        task_sql([])


def test_the_task_is_created_in_the_obs_schema_with_a_schedule_and_a_warehouse():
    ddl = run_macro("it_silence_task_ddl", {"body": "CALL x()", "schema": "my_obs", "warehouse": "my wh", "schedule": "10 MINUTE"})
    assert ddl.startswith('CREATE OR REPLACE TASK "') and '"MY_OBS"."ICECORE_SILENCE"' in ddl
    assert 'WAREHOUSE = "my wh"' in ddl and "SCHEDULE = '10 MINUTE'" in ddl
    assert ddl.endswith("AS\nCALL x()") and "RESUME" not in ddl  # created suspended: resuming is a separate step


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
    ids = {"late": f"model.it.late_{run_id}", "ok": f"model.it.ok_{run_id}", "failed": f"model.it.failed_{run_id}",
           "evil": f"m$$';\\\nx;{run_id}"}
    cur = sf.cursor()
    cur.execute(f"create schema {qs}")
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        # fixed instants, taken once on Snowflake's clock: a view over "now - 90 minutes" would move with every query
        t = {m: cur.execute(f"select dateadd(minute, -{m}, {NOW})::string").fetchone()[0] for m in (5, 10, 90, 200)}
        lit = lambda v: "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"  # noqa: E731
        cur.execute(f"""create view {qs}."STANDIN_NODES" as
            select unique_id, status, recorded_at::timestamp_ntz as recorded_at from values
              ({lit(ids["evil"])}, 'success', '{t[90]}'),
              ('{ids["late"]}', 'success', '{t[90]}'),
              ('{ids["late"]}', 'error', '{t[5]}'),
              ('{ids["ok"]}', 'success', '{t[10]}'),
              ('{ids["failed"]}', 'error', '{t[200]}')
            as t(unique_id, status, recorded_at)""")
        entries = [entry(ids["late"], 60, 1, channel="data-platform", severity="error"), entry(ids["ok"], 60, 1),
                   entry(ids["failed"], 60, 1), entry(ids["evil"], 60, 1, channel=EVIL, severity="warn")]
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
        env["cur"].execute(body)  # the task body is one CALL of ICECORE_SILENCE_CHECK
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


@pytest.mark.sandbox
def test_hostile_sla_text_is_data_end_to_end(silence):
    """A unique_id and a channel with $$, quotes, a backslash, ; and newlines: the CREATE TASK succeeded as one statement
    (the fixture created it), the task keeps the text in its definition, and the verdict carries it back unchanged."""
    assert silence["created"]["status"] == "ok"
    run_task_once(silence)
    (record,) = _records(silence, silence["ids"]["evil"], settle_s=10)
    assert record["record_attributes"]["icecore.silence.channel"] == EVIL
    (task,) = silence["sf"].cursor(DictCursor).execute(
        f'show tasks like \'ICECORE_SILENCE\' in schema {silence["qs"]}').fetchall()
    assert task["definition"].startswith("CALL ") and "GRANT ROLE ACCOUNTADMIN" in task["definition"]  # inside the literal
    assert task["state"] == "suspended"
    # nothing but the one task and its procedure was created in the schema
    objects = silence["sf"].cursor().execute(f"show objects in schema {silence['qs']}").fetchall()
    assert not [o for o in objects if o[1] not in ("ICECORE_SILENCE", "STANDIN_NODES")
                and o[1] not in {"METRICS", "SCHEMA_CHANGES", "INVOCATIONS", "NODE_EXECUTIONS", "SILENCE_EVENTS", "TEST_RESULTS",
                                 "LINEAGE", "LINEAGE_LATEST", "CAPABILITIES"}]


@pytest.mark.sandbox
def test_a_failing_check_raises_so_the_task_history_shows_it(silence):
    with pytest.raises(ProgrammingError):
        silence["cur"].execute(
            f'call {silence["qs"]}."ICECORE_SILENCE_CHECK"(parse_json(\'[]\'), \'NO_SUCH_DB.X.Y\', \'NO_SUCH_DB.X.Z\', \'NO_SUCH_DB.X.E\')')


@pytest.mark.sandbox
def test_recorded_at_of_a_real_build_is_within_seconds_of_snowflakes_clock(sf, run_id):
    from helpers.run_hooks import drop_fixture_views, invocation_records, node_results, obs_schema, run_with_hooks, snowflake_since
    try:
        with obs_schema(sf) as obs:
            obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": obs}})]
            since = snowflake_since(sf)
            res, results = run_with_hooks(["build", "--select", "hooks_a"], run_id, obs)
            assert res.success, res.exception
            inv = results["metadata"]["invocation_id"]
            invocation_records(sf, obs, inv, min_rows=3 + 2 * len(node_results(results)), since=since)
            run_macro("it_create_views", {"schema": obs}, obs_vars)
            lag = sf.cursor().execute(
                f'select max(datediff(second, recorded_at, {NOW})), min(datediff(second, recorded_at, {NOW})) '
                f'from "{DATABASE}"."{obs}"."NODE_EXECUTIONS" where invocation_id = %s and recorded_at is not null', (inv,)).fetchone()
            assert lag[1] >= 0 and lag[0] < 300, lag  # the record landed seconds ago on Snowflake's clock, not dbt's
    finally:
        drop_fixture_views(sf, run_id)
