"""Gate 1 follow-up: do procedure records land when LOG_LEVEL / TRACE_LEVEL are set at SCHEMA or PROCEDURE level?

set_telemetry_levels used to propose ALTER DATABASE ... SET TRACE_LEVEL = 'ALWAYS', which traces every procedure and UDF
the client has in that database. If the levels of the schema that holds the ICECORE_EMIT procedures are enough, that is
the narrower setting.

The sandbox database ICECORE_SANDBOX keeps its own levels (LOG_LEVEL INFO, TRACE_LEVEL ALWAYS): this test never alters
them, and the CI role cannot create a database, so the database's effective level cannot be lowered. What is measured
instead is whether a level set on the schema or on the procedure takes over from the database in BOTH directions. A
schema set to OFF under a database at INFO / ALWAYS silences the procedure (so the schema's value wins), and a
procedure set to INFO / ALWAYS under a schema at OFF lands (so the procedure's value wins). Parameters are inherited
account > database > schema > object, nearest wins; the cases below show that this holds for event-table levels.

RESULT in the ICECORE_CI sandbox (31 Oct 2026): not measurable. `ALTER SCHEMA ... SET LOG_LEVEL / TRACE_LEVEL` and
`ALTER PROCEDURE ... SET LOG_LEVEL / TRACE_LEVEL` fail with 003001 (42501) "must have MODIFY LOG LEVEL / MODIFY TRACE LEVEL
granted on ACCOUNT", although the role owns the schema. The bootstrap grants only MODIFY SESSION LOG LEVEL and
MODIFY SESSION TRACE LEVEL. The test records that as a finding and skips. To measure, an administrator grants the role
`MODIFY LOG LEVEL` and `MODIFY TRACE LEVEL` on the account (sandbox only), then runs this test unchanged.

Each case runs in its own schema ICECORE_SANDBOX.GATE1_LV_<run_id>_<case>, dropped at the end. Results go to
spikes/results/gate1_followups.json.
"""
import time

import pytest
from snowflake.connector.errors import ProgrammingError

from helpers.events import wait_for_events
from helpers.findings import record_finding

DATABASE = "ICECORE_SANDBOX"
SETTLE_S = 75

# case -> (schema levels or None, procedure levels or None)
CASES = {
    "inherit": (None, None),
    "schema_off": ("LOG_LEVEL = 'OFF', TRACE_LEVEL = 'OFF'", None),
    "schema_info_on_event": ("LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ON_EVENT'", None),
    "schema_info_always": ("LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'", None),
    "schema_warn_on_event": ("LOG_LEVEL = 'WARN', TRACE_LEVEL = 'ON_EVENT'", None),
    "schema_off_proc_info_always": ("LOG_LEVEL = 'OFF', TRACE_LEVEL = 'OFF'", "LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'"),
}


def _proc_sql(schema: str, tag: str) -> str:
    return f"""create or replace procedure {DATABASE}.{schema}.EMIT_PROBE()
returns varchar language sql execute as owner as
$$
declare a object;
begin
  a := object_construct('icecore.run_id', '{tag}');
  system$add_event('icecore.lv', :a);
  system$log_info('gate1-lv {tag}');
  return 'ok';
end;
$$"""


@pytest.mark.sandbox
def test_schema_and_procedure_levels_take_over_from_the_database(sf, run_id):
    cur = sf.cursor()
    cur.execute(f"show parameters like '%LEVEL' in database {DATABASE}")
    db_levels = {r[0]: r[1] for r in cur.fetchall()}
    assert db_levels["LOG_LEVEL"] == "INFO" and db_levels["TRACE_LEVEL"] == "ALWAYS", db_levels  # untouched here

    schemas = {case: f"GATE1_LV_{run_id[:12]}_{i}".upper() for i, case in enumerate(CASES)}
    tags = {case: f"{run_id[:12]}-{case}" for case in CASES}
    since = cur.execute("select sysdate()").fetchone()[0]
    probe = f"GATE1_LV_{run_id[:12]}_PROBE".upper()
    cur.execute(f"create schema {DATABASE}.{probe}")
    try:
        cur.execute(f"alter schema {DATABASE}.{probe} set LOG_LEVEL = 'INFO'")
    except ProgrammingError as exc:
        record_finding("gate1_followups", "schema_levels", {
            "run_id": run_id, "database_levels": db_levels, "measured": False, "errno": exc.errno,
            "blocked_statements": ["ALTER SCHEMA SET LOG_LEVEL", "ALTER SCHEMA SET TRACE_LEVEL", "ALTER PROCEDURE SET LOG_LEVEL",
                                   "ALTER PROCEDURE SET TRACE_LEVEL"],
            "reason": str(exc).splitlines()[-1][:200]})
        pytest.skip("the role lacks MODIFY LOG LEVEL / MODIFY TRACE LEVEL on the account: schema levels cannot be set")
    finally:
        cur.execute(f"drop schema if exists {DATABASE}.{probe}")
    try:
        for case, (schema_levels, proc_levels) in CASES.items():
            schema = schemas[case]
            cur.execute(f"create schema {DATABASE}.{schema}")
            if schema_levels:
                cur.execute(f"alter schema {DATABASE}.{schema} set {schema_levels}")
            cur.execute(_proc_sql(schema, tags[case]))
            if proc_levels:
                cur.execute(f"alter procedure {DATABASE}.{schema}.EMIT_PROBE() set {proc_levels}")
            assert cur.execute(f"call {DATABASE}.{schema}.EMIT_PROBE()").fetchone()[0] == "ok"

        # the control must land; then wait so that absence is a measurement, not a race with ingestion
        wait_for_events(sf, "record_type = 'LOG' and value::string = %(m)s", {"m": f"gate1-lv {tags['inherit']}"},
                        timeout_s=240, settle_s=SETTLE_S)
        found = {}
        for case, tag in tags.items():
            cur2 = sf.cursor()
            n_logs = cur2.execute(
                "select count(*) from ICECORE_SANDBOX.TELEMETRY.EVENTS where timestamp >= %s and record_type = 'LOG' "
                "and value::string = %s", (since, f"gate1-lv {tag}")).fetchone()[0]
            n_events = cur2.execute(
                "select count(*) from ICECORE_SANDBOX.TELEMETRY.EVENTS where timestamp >= %s and record_type = 'SPAN_EVENT' "
                "and record_attributes:\"icecore.run_id\" = %s", (since, tag)).fetchone()[0]
            found[case] = {"log_records": n_logs, "span_events": n_events}
        record_finding("gate1_followups", "schema_levels", {
            "run_id": run_id, "database_levels": db_levels, "measured": True, "settle_s": SETTLE_S,
            "cases": {c: {"schema": CASES[c][0], "procedure": CASES[c][1], **found[c]} for c in CASES}})

        assert found["inherit"] == {"log_records": 1, "span_events": 1}, found
        assert found["schema_off"] == {"log_records": 0, "span_events": 0}, found          # the schema wins over the database
        assert found["schema_info_always"] == {"log_records": 1, "span_events": 1}, found
        assert found["schema_info_on_event"] == {"log_records": 1, "span_events": 1}, found
        assert found["schema_off_proc_info_always"] == {"log_records": 1, "span_events": 1}, found  # the procedure wins over the schema
    finally:
        for schema in schemas.values():
            cur.execute(f"drop schema if exists {DATABASE}.{schema}")
