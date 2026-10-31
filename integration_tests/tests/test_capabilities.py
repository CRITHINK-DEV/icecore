"""UC5 capabilities: icecore.detect_capabilities (macros/setup/detect_capabilities.sql).

Detection reads, each read guarded so that none can fail the run:
- the edition, from SNOWFLAKE.ORGANIZATION_USAGE.ACCOUNTS for the current account, else inferred as ENTERPRISE when
  the database has an event table of its own (a database-level event table needs Enterprise), else UNKNOWN;
- the database's EVENT_TABLE (and the level it is set at), LOG_LEVEL and TRACE_LEVEL, from SHOW PARAMETERS;
- Cortex anomaly detection: the class SNOWFLAKE.ML.ANOMALY_DETECTION visible to the role (SHOW CLASSES).
It returns a dict (the contract set_telemetry_levels reads), prints the vars block for dbt_project.yml and records a
capabilities span event through icecore.emit (UC1), so it lands in the event table with icecore.capability.* keys.

Sandbox tests that record create both procedures in a schema of their own (ICECORE_OBS_<id>, passed as the obs_schema
var), read the event table from a window opened on Snowflake's clock, and drop the schema at the end.
"""
import json
import os
import uuid

import pytest
import yaml

from helpers.events import wait_for_events
from helpers.macros import run_macro, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()


def with_vars(icecore_vars: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": icecore_vars})]


def call(name: str, kwargs: dict | None = None, extra: list[str] | None = None):
    return run_macro_with_logs("it_call", {"name": name, "kwargs": kwargs or {}}, extra)


def caps(cortex=True, **overrides):
    out = {"database": "OBS_DB", "edition": "ENTERPRISE", "edition_source": "organization_usage",
           "event_table": "OBS_DB.TELEMETRY.EVENTS", "event_table_level": "DATABASE",
           "log_level": "INFO", "trace_level": "ALWAYS", "cortex": cortex, "cortex_reason": None}
    out.update(overrides)
    return out


def vars_block(text: str) -> dict:
    return yaml.safe_load(text[text.index("vars:"):])


# --- offline: no connection ----------------------------------------------------------------------

def test_vars_block_with_cortex():
    out, _ = call("capabilities_vars_block", {"capabilities": caps(cortex=True)})
    assert vars_block(out)["vars"]["icecore"] == {"obs_schema": "ICECORE_OBS", "anomaly_engine": "auto",
                                                  "shadow_engine": "none"}


def test_vars_block_without_cortex_pins_the_native_engine():
    out, _ = call("capabilities_vars_block", {"capabilities": caps(cortex=False, cortex_reason="no access")})
    assert vars_block(out)["vars"]["icecore"]["anomaly_engine"] == "native"


def test_vars_block_keeps_the_obs_schema_var():
    out, _ = call("capabilities_vars_block", {"capabilities": caps()}, with_vars({"obs_schema": "team_obs"}))
    assert vars_block(out)["vars"]["icecore"]["obs_schema"] == "TEAM_OBS"


@pytest.mark.parametrize("org_edition,event_table_level,expected", [
    ("ENTERPRISE", "", ("ENTERPRISE", "organization_usage")),
    ("standard", "", ("STANDARD", "organization_usage")),
    ("BUSINESS_CRITICAL", "DATABASE", ("BUSINESS_CRITICAL", "organization_usage")),
    (None, "DATABASE", ("ENTERPRISE", "database_event_table")),
    (None, "", ("UNKNOWN", "unknown")),
    (None, "ACCOUNT", ("UNKNOWN", "unknown")),
])
def test_edition(org_edition, event_table_level, expected):
    out, _ = call("_icecore_edition", {"org_edition": org_edition, "event_table_level": event_table_level})
    assert tuple(out) == expected


def test_capability_attributes_use_the_icecore_capability_keys():
    out, _ = call("_icecore_capability_attributes", {"capabilities": caps(cortex=False, cortex_reason="no access")})
    assert out == {
        "icecore.capability.database": "OBS_DB",
        "icecore.capability.edition": "ENTERPRISE",
        "icecore.capability.edition_source": "organization_usage",
        "icecore.capability.event_table": "OBS_DB.TELEMETRY.EVENTS",
        "icecore.capability.event_table_level": "DATABASE",
        "icecore.capability.log_level": "INFO",
        "icecore.capability.trace_level": "ALWAYS",
        "icecore.capability.cortex": False,
        "icecore.capability.cortex_reason": "no access",
    }


# --- sandbox --------------------------------------------------------------------------------------

def snowflake_since(sf, minutes: int = 5):
    """A window start on Snowflake's clock, in UTC like the event table's TIMESTAMP (the laptop runs ~10 s ahead)."""
    return sf.cursor().execute(
        f"select dateadd(minute, -{minutes}, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]


@pytest.fixture(scope="module")
def obs(dbt, sf):
    """Both emitter procedures (icecore.create_emit_procedures) in a schema of their own; dropped afterwards."""
    schema = f"ICECORE_OBS_{uuid.uuid4().hex}".upper()
    sf.cursor().execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, with_vars({"obs_schema": schema}))
        yield {"schema": schema, "vars": with_vars({"obs_schema": schema})}
    finally:
        sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{schema}"')


@pytest.mark.sandbox
def test_detects_the_sandbox_and_prints_the_vars_block(dbt, sf):
    params = {r[0]: r for r in sf.cursor().execute(f"show parameters in database {DATABASE}").fetchall()}
    out, logs = call("detect_capabilities", {"record": False})
    assert out["database"] == DATABASE
    assert out["edition"] == "ENTERPRISE", out
    assert out["event_table"] == params["EVENT_TABLE"][1] == "ICECORE_SANDBOX.TELEMETRY.EVENTS"
    assert out["event_table_level"] == "DATABASE"
    assert (out["log_level"], out["trace_level"]) == (params["LOG_LEVEL"][1], params["TRACE_LEVEL"][1])
    assert out["cortex"] is True and out["cortex_reason"] is None, out
    assert out["recorded"] is None
    printed = next(line for line in logs if "vars:" in line)
    assert vars_block(printed)["vars"]["icecore"]["anomaly_engine"] == "auto"


@pytest.mark.sandbox
def test_capabilities_record_lands(sf, obs):
    since = snowflake_since(sf)
    out, _ = call("detect_capabilities", {}, obs["vars"])
    assert out["recorded"]["statuses"] == ["ok 1"], out
    where = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
             " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
             " and startswith(resource_attributes:\"snow.executable.name\"::string, 'ICECORE_EMIT')"
             " and record:name::string = 'icecore.capabilities'")
    rows = wait_for_events(sf, where, {"db": DATABASE, "schema": obs["schema"]}, since=since)
    assert len(rows) == 1
    a = rows[0]["record_attributes"]
    assert rows[0]["record_type"] == "SPAN_EVENT"
    assert a["icecore.capability.edition"] == "ENTERPRISE"
    assert a["icecore.capability.event_table"] == "ICECORE_SANDBOX.TELEMETRY.EVENTS"
    assert a["icecore.capability.cortex"] is True
    assert a["icecore.capability.log_level"] == out["log_level"]
    assert a["icecore.capability.trace_level"] == out["trace_level"]
    assert a["dbt.invocation_id"]


@pytest.mark.sandbox
def test_cortex_is_false_when_the_role_cannot_see_snowflake_ml(dbt, run_id):
    # A schema the role cannot see answers exactly as one it lacks privileges on: 002003 "does not exist or
    # not authorized". The sandbox has no role without access to SNOWFLAKE.ML, so the class is pointed at one.
    hidden = f"SNOWFLAKE.ML_HIDDEN_{run_id}.ANOMALY_DETECTION".upper()
    out, logs = call("detect_capabilities", {"record": False, "cortex_class": hidden})
    assert out["cortex"] is False
    assert "does not exist or not authorized" in out["cortex_reason"], out
    assert out["edition"] == "ENTERPRISE" and out["event_table"], out
    assert vars_block(next(line for line in logs if "vars:" in line))["vars"]["icecore"]["anomaly_engine"] == "native"
