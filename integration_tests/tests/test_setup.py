"""UC5 setup and doctor: icecore.setup (macros/setup/setup.sql) and icecore.doctor (macros/setup/doctor.sql).

setup runs one ordered list of steps: detect_capabilities, CREATE SCHEMA IF NOT EXISTS for the obs schema,
create_emit_procedures, set_telemetry_levels, then records the capabilities through the new ICECORE_EMIT. The
steps that do not exist yet (create_alerts, create_tasks, deploy_app, compat views) keep their place
in the list and are reported as planned. The `schema` argument overrides the obs_schema var.

doctor re-detects, checks the obs schema's levels (LOG_LEVEL at least INFO, TRACE_LEVEL ALWAYS), makes
one guarded test CALL of each procedure tagged icecore.doctor.probe, and counts icecore records of the last 24 hours
through base_events() on Snowflake's clock, its own probes and capabilities records excluded. It reports and never
fails the run.

Sandbox tests use a schema ICECORE_OBS_<run_id> of their own (the `schema` argument, and the obs_schema var where
the run's emitters must write there), never ALTER the shared ICECORE_SANDBOX database, and drop the schema at the end.
"""
import json
import os
import re
from pathlib import Path

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import IT_DIR, MacroFailed, call_macro, run_macro, run_macro_with_logs

REPO = IT_DIR.parent
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
TABLE = "ICECORE_SANDBOX.TELEMETRY.EVENTS"


def with_vars(icecore_vars: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": icecore_vars})]


def call(name: str, kwargs: dict | None = None, extra: list[str] | None = None):
    return run_macro_with_logs("it_call", {"name": name, "kwargs": kwargs or {}}, extra)


def caps(**overrides):
    out = {"database": "OBS_DB", "edition": "ENTERPRISE", "edition_source": "organization_usage",
           "event_table": "OBS_DB.TELEMETRY.EVENTS", "event_table_level": "DATABASE",
           "log_level": "INFO", "trace_level": "ALWAYS", "cortex": True, "cortex_reason": None}
    out.update(overrides)
    return out


OK_PROBES = {"ICECORE_EMIT": "ok 1", "ICECORE_EMIT_RUN": "ok spans=0 logs=1"}


# --- offline: no connection ----------------------------------------------------------------------

def test_setup_steps_keep_one_ordered_list():
    steps = call_macro("_icecore_setup_steps")
    assert [s["name"] for s in steps] == [
        "detect_capabilities", "create_schema", "create_emit_procedures", "set_telemetry_levels",
        "record_capabilities", "estimate_telemetry_cost", "create_views", "create_alerts", "create_tasks"]
    assert [s["name"] for s in steps if s["ready"]] == [
        "detect_capabilities", "create_schema", "create_emit_procedures", "set_telemetry_levels", "record_capabilities",
        "estimate_telemetry_cost", "create_views"]


def test_setup_steps_add_compat_views_and_the_app_on_request():
    steps = call_macro("_icecore_setup_steps", compat="elementary", app=True)
    names = [s["name"] for s in steps]
    assert names.index("create_views") < names.index("create_compat_views") < names.index("deploy_app")
    assert not any(s["ready"] for s in steps if s["name"] in ("create_compat_views", "deploy_app"))


def test_schema_argument_overrides_the_obs_schema_var():
    out, _ = call("obs_schema_name", {"schema": "icecore_obs_x"}, with_vars({"obs_schema": "team_obs"}))
    assert out == "ICECORE_OBS_X"
    out, _ = call("obs_schema", {"schema": "icecore_obs_x"})
    assert out == f'"{DATABASE}"."ICECORE_OBS_X"'
    out, _ = call("obs_schema_name", {}, with_vars({"obs_schema": "team_obs"}))
    assert out == "TEAM_OBS"


def test_procedures_keep_their_grants_when_replaced():
    for ddl in run_macro("it_procedure_ddl", {}):
        assert re.search(r"\(\w+ ARRAY\)\s+COPY GRANTS\s+RETURNS STRING", ddl), ddl


def test_one_plain_identifier_helper():
    """The three copies (_icecore_identifier, _icecore_levels_identifier, _icecore_capability_identifier) are one."""
    sources = "\n".join(p.read_text() for p in (REPO / "macros").rglob("*.sql"))
    assert "_icecore_levels_identifier" not in sources
    assert "_icecore_capability_identifier" not in sources
    assert len(re.findall(r"macro _icecore_identifier\(", sources)) == 1
    assert call("_icecore_identifier", {"name": "obs_db"})[0] == "obs_db"
    assert call("_icecore_identifier", {"name": 'My "Obs" db'})[0] == '"My ""Obs"" db"'


def test_doctor_count_sql_reads_base_events_for_24_hours_on_snowflakes_clock():
    sql = call_macro("_icecore_doctor_count_sql", event_table="OBS_DB.TELEMETRY.EVENTS", database="OBS_DB",
                     schema="icecore_obs_x")
    assert "from OBS_DB.TELEMETRY.EVENTS" in sql
    assert "RESOURCE_ATTRIBUTES:\"snow.schema.name\"::string = 'ICECORE_OBS_X'" in sql
    assert "RESOURCE_ATTRIBUTES:\"snow.database.name\"::string = 'OBS_DB'" in sql
    assert "dateadd(hour, -24, convert_timezone('UTC', current_timestamp())::timestamp_ntz)" in sql
    assert "icecore.doctor.probe" in sql and "'icecore.capabilities'" in sql


def test_doctor_reports_ok_with_the_count():
    r = call_macro("_icecore_doctor_report", capabilities=caps(), probes=OK_PROBES, count=42)
    assert r["status"] == "ok"
    assert r["records_24h"] == 42
    assert r["message"].startswith("OK") and "42 icecore records in the last 24 h" in r["message"]


def test_doctor_without_records_names_the_hooks():
    r = call_macro("_icecore_doctor_report", capabilities=caps(), probes=OK_PROBES, count=0)
    assert r["status"] == "no_records" and r["cause"] == "hooks"
    assert "no records in 24 h" in r["message"]
    assert "icecore.start_run()" in r["message"] and "icecore.end_run(results)" in r["message"]


@pytest.mark.parametrize("overrides,probes,cause", [
    ({"event_table": ""}, OK_PROBES, "event_table"),
    ({"log_level": "WARN"}, OK_PROBES, "levels"),
    ({"trace_level": "OFF"}, OK_PROBES, "levels"),
    ({}, {"ICECORE_EMIT": "icecore-error 2141: Unknown function", "ICECORE_EMIT_RUN": "ok spans=0 logs=1"}, "procedures"),
    ({}, {"ICECORE_EMIT": "ok 1", "ICECORE_EMIT_RUN": "icecore-error 100357: Python error"}, "procedures"),
])
def test_doctor_without_records_names_an_earlier_cause_first(overrides, probes, cause):
    r = call_macro("_icecore_doctor_report", capabilities=caps(**overrides), probes=probes, count=0)
    assert r["status"] == "no_records" and r["cause"] == cause, r


def test_doctor_with_records_and_a_failed_check_warns():
    r = call_macro("_icecore_doctor_report", capabilities=caps(trace_level="OFF"), probes=OK_PROBES, count=7)
    assert r["status"] == "warn" and r["records_24h"] == 7
    assert "TRACE_LEVEL" in r["message"]
    assert [c["name"] for c in r["checks"] if not c["ok"]] == ["levels"]


def test_doctor_checks_the_levels_effective_on_the_schema():
    narrow = {"log_level": "INFO", "trace_level": "ALWAYS"}
    r = call_macro("_icecore_doctor_report", capabilities=caps(log_level="OFF", trace_level="OFF"), probes=OK_PROBES,
                   count=3, schema_levels=narrow)
    assert r["status"] == "ok", r
    r = call_macro("_icecore_doctor_report", capabilities=caps(), probes=OK_PROBES, count=3,
                   schema_levels={"log_level": "INFO", "trace_level": "ON_EVENT"})
    levels = next(c for c in r["checks"] if c["name"] == "levels")
    assert not levels["ok"] and "ALTER SCHEMA" in levels["detail"] and "ALWAYS" in levels["detail"], levels


def test_doctor_reports_a_count_it_could_not_read():
    r = call_macro("_icecore_doctor_report", capabilities=caps(), probes=OK_PROBES, count=None,
                   count_error="Insufficient privileges")
    assert r["status"] == "warn" and r["records_24h"] is None
    assert "Insufficient privileges" in r["message"]


# --- sandbox --------------------------------------------------------------------------------------

def _levels_are_sufficient(sf) -> bool:
    rows = {r[0]: r[1] for r in sf.cursor().execute(f"show parameters in database {DATABASE}").fetchall()}
    return rows.get("LOG_LEVEL") in ("INFO", "DEBUG", "TRACE") and rows.get("TRACE_LEVEL") == "ALWAYS"


@pytest.fixture
def fresh_schema(sf, run_id):
    """A run_id-named obs schema that does not exist yet; dropped afterwards. Skips rather than let setup try to
    ALTER the shared database's levels."""
    if not _levels_are_sufficient(sf):
        pytest.skip(f"{DATABASE} levels are not sufficient; setup would try to ALTER the new schema's levels")
    schema = f"ICECORE_OBS_{run_id}".upper()
    try:
        yield schema
    finally:
        sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def _state(sf, schema):
    cur = sf.cursor(DictCursor)
    shown = cur.execute(f"show schemas like '{schema}' in database {DATABASE}").fetchall()
    state = {"schema_created_on": [r["created_on"] for r in shown]}
    for proc in ("ICECORE_EMIT", "ICECORE_EMIT_RUN"):
        qualified = f'"{DATABASE}"."{schema}"."{proc}"(ARRAY)'
        state[proc + "_ddl"] = cur.execute(f"select get_ddl('procedure', '{qualified}')").fetchone()
        grants = cur.execute(f"show grants on procedure {qualified}").fetchall()
        state[proc + "_grants"] = sorted((g["privilege"], g["granted_to"], g["grantee_name"]) for g in grants)
    for view in sorted(r["name"] for r in cur.execute(f'show views in schema "{DATABASE}"."{schema}"').fetchall()):
        qualified = f'"{DATABASE}"."{schema}"."{view}"'
        state[view + "_ddl"] = cur.execute(f"select get_ddl('view', '{qualified}')").fetchone()
        grants = cur.execute(f"show grants on view {qualified}").fetchall()
        state[view + "_grants"] = sorted((g["privilege"], g["granted_to"], g["grantee_name"]) for g in grants)
    return state


def _snowflake_since(sf, minutes: int = 5):
    return sf.cursor().execute(
        f"select dateadd(minute, -{minutes}, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]


@pytest.mark.sandbox
def test_setup_twice_changes_nothing(sf, fresh_schema):
    """AC1: given a fresh sandbox schema, when setup runs twice, the second run succeeds and changes nothing."""
    schema = fresh_schema
    assert not sf.cursor().execute(f"show schemas like '{schema}' in database {DATABASE}").fetchall()

    # the user-facing command, as the README gives it
    _, logs = run_macro_with_logs("icecore.setup", {"schema": schema, "compat": "none", "app": False})
    assert any("create_views" in line and "METRICS" in line and "planned" not in line for line in logs), logs
    # a grant made after setup must survive the next setup (CREATE OR REPLACE ... COPY GRANTS)
    sf.cursor().execute(f'grant usage on procedure "{DATABASE}"."{schema}"."ICECORE_EMIT"(ARRAY) to role PUBLIC')
    sf.cursor().execute(f'grant select on view "{DATABASE}"."{schema}"."METRICS" to role PUBLIC')  # COPY GRANTS keeps it
    before = _state(sf, schema)
    assert before["schema_created_on"] and ("USAGE", "ROLE", "PUBLIC") in before["ICECORE_EMIT_grants"]
    assert ("SELECT", "ROLE", "PUBLIC") in before["METRICS_grants"]
    assert {"METRICS_ddl", "SCHEMA_CHANGES_ddl"} <= set(before)  # the views exist, with their DDL compared too

    first, _ = call("setup", {"schema": schema})
    second, _ = call("setup", {"schema": schema})
    after = _state(sf, schema)

    assert after == before
    for out in (first, second):
        assert out["schema"] == schema
        assert out["levels"]["executed"] == [] and out["levels"]["levels_ok"] is True, out["levels"]
        assert [s["name"] for s in out["steps"] if s["status"] == "done"] == [
            "detect_capabilities", "create_schema", "create_emit_procedures", "set_telemetry_levels",
            "record_capabilities", "estimate_telemetry_cost", "create_views"], out["steps"]
        assert out["recorded"] == "ok 1"
    assert first["levels"] == second["levels"]
    assert first["steps"] == second["steps"]


@pytest.mark.sandbox
def test_doctor_without_records_names_the_hooks_and_its_probes_land_tagged(sf, fresh_schema):
    """AC3: no icecore records for 24 hours -> the telemetry hooks are named as the likely cause."""
    schema = fresh_schema
    call("setup", {"schema": schema})
    since = _snowflake_since(sf)
    out, logs = call("doctor", {"schema": schema})
    assert out["status"] == "no_records" and out["cause"] == "hooks", out
    assert out["records_24h"] == 0
    assert out["probes"] == OK_PROBES, out["probes"]
    assert any("no records in 24 h" in line and "icecore.end_run(results)" in line for line in logs), logs

    where = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
             " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
             " and record_attributes:\"icecore.doctor.probe\"::boolean")
    rows = wait_for_events(sf, where, {"db": DATABASE, "schema": schema}, min_rows=2, since=since, settle_s=10)
    assert sorted((r["record_type"], r["resource_attributes"]["snow.executable.name"].split("(")[0]) for r in rows) == [
        ("LOG", "ICECORE_EMIT_RUN"), ("SPAN_EVENT", "ICECORE_EMIT")], rows

    # the probes are in the event table now, and doctor still does not count them
    again, _ = call("doctor", {"schema": schema})
    assert again["records_24h"] == 0 and again["cause"] == "hooks", again


@pytest.mark.sandbox
def test_doctor_reports_ok_with_the_count(sf, fresh_schema, run_id):
    """AC2: recent icecore records -> OK with the count."""
    schema = fresh_schema
    call("setup", {"schema": schema})
    since = _snowflake_since(sf)
    emitted = run_macro("it_emit", {"kind": "doctor_fixture", "attributes": {"icecore.run_id": run_id}},
                        with_vars({"obs_schema": schema}))
    assert emitted["statuses"] == ["ok 1"], emitted
    wait_for_events(sf, "record_attributes:\"icecore.run_id\"::string = %(r)s", {"r": run_id}, since=since)

    out, logs = call("doctor", {"schema": schema})
    assert out["status"] == "ok", out
    assert out["records_24h"] == 1
    assert any(line.startswith("icecore: doctor: OK") and "1 icecore records in the last 24 h" in line
               for line in logs), logs


@pytest.mark.sandbox
def test_doctor_never_fails_when_the_procedures_are_missing(sf, run_id):
    schema = f"ICECORE_OBS_NOPE_{run_id}".upper()
    out, _ = call("doctor", {"schema": schema})
    assert out["status"] == "no_records" and out["cause"] == "procedures", out
    assert all(s.startswith("icecore-error") for s in out["probes"].values()), out["probes"]
    assert not sf.cursor().execute(f"show schemas like '{schema}' in database {DATABASE}").fetchall()
