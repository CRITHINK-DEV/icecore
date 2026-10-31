"""UC5 telemetry levels: icecore.telemetry_levels_plan (pure) and icecore.set_telemetry_levels.

Levels are the target database's, never the session's or the account's (spikes/GATE1.md): LOG_LEVEL at least INFO,
TRACE_LEVEL ON_EVENT or ALWAYS. The only statement icecore may run is the database's ALTER DATABASE ... SET LOG_LEVEL /
TRACE_LEVEL, inside a guard; anything else (ALTER ACCOUNT, the database's EVENT_TABLE, CREATE EVENT TABLE, clustering)
is printed for an admin.

The edition paths are tested offline with mocked detection results. The sandbox tests never ALTER the shared
ICECORE_SANDBOX database: one reads its current levels and checks that nothing is changed, one aims the guarded ALTER
at a database that does not exist, and one runs the printed DDL for a dedicated event table in a run_id-named schema
to show it is valid SQL.
"""
import os

import pytest

from helpers.macros import call_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
ACCOUNT_TABLE = "SNOWFLAKE.TELEMETRY.EVENTS"


def caps(edition="ENTERPRISE", event_table=ACCOUNT_TABLE, log_level="INFO", trace_level="ALWAYS"):
    return {"edition": edition, "event_table": event_table, "log_level": log_level, "trace_level": trace_level}


def plan(capabilities, database="OBS_DB", dedicated_event_table=None):
    return call_macro("telemetry_levels_plan", capabilities=capabilities, database=database,
                      dedicated_event_table=dedicated_event_table)


def joined(statements) -> str:
    return "\n".join(statements).upper()


# --- offline: mocked detection results -----------------------------------------------------------

def test_standard_without_event_table_prints_alter_account_and_never_runs_it():
    p = plan(caps(edition="STANDARD", event_table="", log_level="OFF", trace_level="OFF"))
    assert "ALTER ACCOUNT SET EVENT_TABLE = SNOWFLAKE.TELEMETRY.EVENTS" in joined(p["print"])
    assert "ALTER ACCOUNT" not in joined(p["attempt"])
    # the levels belong to the database; that statement may be attempted (guarded)
    assert p["attempt"] == ["ALTER DATABASE OBS_DB SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'"]


def test_standard_uses_the_account_table_even_when_a_dedicated_one_is_requested():
    p = plan(caps(edition="STANDARD"), dedicated_event_table="OBS_DB.TELEMETRY.EVENTS")
    assert p["attempt"] == []
    assert "SET EVENT_TABLE" not in joined(p["print"])
    assert "ALTER ACCOUNT" not in joined(p["attempt"] + p["print"])
    assert any("Enterprise" in n for n in p["notes"]), p["notes"]


@pytest.mark.parametrize("edition", ["ENTERPRISE", "BUSINESS_CRITICAL"])
def test_enterprise_with_a_dedicated_table_prints_the_database_association(edition):
    p = plan(caps(edition=edition), dedicated_event_table="OBS_DB.TELEMETRY.EVENTS")
    printed = joined(p["print"])
    assert "CREATE EVENT TABLE IF NOT EXISTS OBS_DB.TELEMETRY.EVENTS" in printed
    assert "ALTER DATABASE OBS_DB SET EVENT_TABLE = OBS_DB.TELEMETRY.EVENTS" in printed
    assert "ALTER TABLE OBS_DB.TELEMETRY.EVENTS CLUSTER BY (TO_DATE(TIMESTAMP), RECORD_TYPE)" in printed
    assert p["attempt"] == []


def test_enterprise_dedicated_table_already_associated_prints_no_association():
    p = plan(caps(event_table="OBS_DB.TELEMETRY.EVENTS"), dedicated_event_table="obs_db.telemetry.events")
    assert "SET EVENT_TABLE" not in joined(p["print"])
    assert "CREATE EVENT TABLE" not in joined(p["print"])


def test_custom_event_table_gets_the_clustering_recommendation():
    p = plan(caps(edition="STANDARD", event_table="OPS.TELEMETRY.EVENTS"))
    assert "ALTER TABLE OPS.TELEMETRY.EVENTS CLUSTER BY (TO_DATE(TIMESTAMP), RECORD_TYPE)" in joined(p["print"])


def test_account_table_note_names_events_viewer():
    p = plan(caps())
    assert any("EVENTS_VIEWER" in n for n in p["notes"]), p["notes"]


@pytest.mark.parametrize("log_level,trace_level,expected", [
    ("WARN", "ALWAYS", "ALTER DATABASE OBS_DB SET LOG_LEVEL = 'INFO'"),
    ("INFO", "OFF", "ALTER DATABASE OBS_DB SET TRACE_LEVEL = 'ALWAYS'"),
    ("off", "off", "ALTER DATABASE OBS_DB SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'"),
    (None, None, "ALTER DATABASE OBS_DB SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'"),
])
def test_insufficient_levels_set_only_what_is_missing(log_level, trace_level, expected):
    assert plan(caps(log_level=log_level, trace_level=trace_level))["attempt"] == [expected]


@pytest.mark.parametrize("log_level,trace_level", [
    ("INFO", "ALWAYS"), ("DEBUG", "ON_EVENT"), ("TRACE", "ALWAYS"), ("info", "on_event"),
])
def test_sufficient_levels_change_nothing(log_level, trace_level):
    out = call_macro("set_telemetry_levels", capabilities=caps(log_level=log_level, trace_level=trace_level),
                     database="OBS_DB")
    assert out["levels_ok"] is True
    assert out["executed"] == [] and out["printed"] == [], out


def test_apply_false_prints_instead_of_running():
    out = call_macro("set_telemetry_levels", capabilities=caps(edition="STANDARD", event_table="", log_level="OFF"),
                     database="OBS_DB", apply=False)
    assert out["executed"] == []
    assert "ALTER DATABASE OBS_DB SET LOG_LEVEL = 'INFO'" in joined(out["printed"])
    assert "ALTER ACCOUNT SET EVENT_TABLE" in joined(out["printed"])


def test_quoted_database_name():
    p = plan(caps(log_level="OFF"), database='My "Obs" db')
    assert p["attempt"] == ["""ALTER DATABASE "My ""Obs"" db" SET LOG_LEVEL = 'INFO'"""]


def test_missing_capabilities_is_a_clear_error():
    with pytest.raises(Exception, match="detect_capabilities"):
        call_macro("set_telemetry_levels", capabilities=None)


# --- sandbox: read-only on ICECORE_SANDBOX ---------------------------------------------------------

def _levels(sf, database):
    rows = sf.cursor().execute(f"show parameters in database {database}").fetchall()
    return {r[0]: r[1] for r in rows if r[0] in ("LOG_LEVEL", "TRACE_LEVEL", "EVENT_TABLE")}


@pytest.mark.sandbox
def test_sandbox_levels_are_sufficient_and_nothing_changes(dbt, sf):
    before = _levels(sf, DATABASE)
    detected = caps(edition="UNKNOWN", event_table=before["EVENT_TABLE"], log_level=before["LOG_LEVEL"],
                    trace_level=before["TRACE_LEVEL"])
    out = call_macro("set_telemetry_levels", capabilities=detected, database=DATABASE)
    assert out["levels_ok"] is True, out
    assert out["executed"] == [] and "ALTER DATABASE" not in joined(out["printed"]), out
    assert _levels(sf, DATABASE) == before


@pytest.mark.sandbox
def test_guarded_alter_that_fails_is_printed_and_the_run_succeeds(dbt, run_id):
    missing = f"ICECORE_NOPE_{run_id}".upper()
    out = call_macro("set_telemetry_levels", capabilities=caps(log_level="OFF", trace_level="OFF"), database=missing)
    assert out["executed"] == []
    assert f"ALTER DATABASE {missing} SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'" in joined(out["printed"])
    assert any("icecore-error" in n for n in out["notes"]), out["notes"]


@pytest.mark.sandbox
def test_printed_dedicated_table_ddl_is_valid_sql(dbt, sf, run_id):
    schema = f"UC5_TL_{run_id}".upper()
    table = f"{DATABASE}.{schema}.EVENTS"
    p = plan(caps(), database=DATABASE, dedicated_event_table=table)
    ddl = [s for s in p["print"] if s.upper().startswith(("CREATE EVENT TABLE", "ALTER TABLE"))]
    assert len(ddl) == 2, p["print"]
    cur = sf.cursor()
    cur.execute(f"create schema {DATABASE}.{schema}")
    try:
        for statement in ddl:
            cur.execute(statement)
        cur.execute(f"show event tables like 'EVENTS' in schema {DATABASE}.{schema}")
        shown = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        assert len(shown) == 1, shown
        assert shown[0]["cluster_by"].upper() == "LINEAR(TO_DATE(TIMESTAMP), RECORD_TYPE)", shown
    finally:
        cur.execute(f"drop schema if exists {DATABASE}.{schema} cascade")
