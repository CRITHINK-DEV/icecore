"""Gate 1 open item 7: do the icecore procedures' records land when LOG_LEVEL / TRACE_LEVEL are set below the database?

set_telemetry_levels used to propose ALTER DATABASE ... SET TRACE_LEVEL = 'ALWAYS', which traces every procedure and UDF
the client has in that database. If the levels of the schema that holds ICECORE_EMIT and ICECORE_EMIT_RUN (or of the
procedures) are enough, that is the narrower setting.

Each case builds a schema ICECORE_SANDBOX.GATE1_LV_<run_id>_<n> with the real ICECORE_EMIT and ICECORE_EMIT_RUN (the DDL of
icecore._icecore_emit_procedures_ddl) and a probe procedure that adds no event, sets the case's levels on the schema or on
the procedures, calls all three, and counts what lands. The schema is dropped at the end. Records are attributed to a case
by the schema name of the record's resource attributes, so nothing is read by "latest rows".

How the database's own levels are controlled for: the sandbox database ICECORE_SANDBOX keeps LOG_LEVEL INFO and
TRACE_LEVEL ALWAYS, which are already enough and could mask any narrower setting. The test never alters them (the CI role
cannot create a database to lower them) and proves instead that the lower level is the effective one:
  - `schema_off` under INFO / ALWAYS lands nothing: the schema's value overrides the database's.
  - the probe procedure adds no event, so it lands a SPAN record only when the effective TRACE_LEVEL is ALWAYS. Under
    ON_EVENT it lands none. That separates ON_EVENT from ALWAYS even though the database says ALWAYS.
  - a procedure's own level overrides the schema's (`schema_off_proc_*`).
Results go to spikes/results/gate1_followups.json (key `levels_matrix`).
"""
import datetime as dt
import json
import time

import pytest
from snowflake.connector import DictCursor

from helpers.events import EVENT_TABLE
from helpers.findings import record_finding
from helpers.macros import call_macro

DATABASE = "ICECORE_SANDBOX"
SETTLE_S = 90
TIMEOUT_S = 300

LV = "LOG_LEVEL = '{l}', TRACE_LEVEL = '{t}'"
OFF = LV.format(l="OFF", t="OFF")

# case -> (schema levels or None, procedure levels or None)
CASES = {
    "inherit": (None, None),
    "schema_off": (OFF, None),
    "schema_info_on_event": (LV.format(l="INFO", t="ON_EVENT"), None),
    "schema_info_always": (LV.format(l="INFO", t="ALWAYS"), None),
    "schema_info_off": (LV.format(l="INFO", t="OFF"), None),
    "schema_off_on_event": (LV.format(l="OFF", t="ON_EVENT"), None),
    "schema_warn_on_event": (LV.format(l="WARN", t="ON_EVENT"), None),
    "schema_off_proc_info_always": (OFF, LV.format(l="INFO", t="ALWAYS")),
    "schema_off_proc_info_on_event": (OFF, LV.format(l="INFO", t="ON_EVENT")),
}

PROBE = "EMIT_PROBE_NO_EVENT"
LOG_MESSAGE = "gate1-lv"
EMIT_ARGS = [{"name": "lv", "attrs": {"icecore.case": "x"}}]


def _run_args() -> list:
    """Two explicit spans (one carries an event, one does not) and one log, timed now so the records fall in the read window."""
    start_ns = (int(time.time()) - 5) * 1_000_000_000
    end_ns = start_ns + 1_000_000_000
    return [{"name": "icecore.lv.span", "start_ns": start_ns, "end_ns": end_ns, "attrs": {"icecore.case": "x"}},
            {"name": "icecore.lv.span_with_event", "start_ns": start_ns, "end_ns": end_ns, "attrs": {"icecore.case": "x"},
             "events": [{"name": "icecore.lv.inner", "ts_ns": start_ns + 500_000_000}]},
            {"type": "log", "level": "info", "message": LOG_MESSAGE, "attrs": {"icecore.case": "x"}}]


def _qualified(schema: str, name: str) -> str:
    return f"{DATABASE}.{schema}.{name}"


def _create(cur, schema: str) -> None:
    cur.execute(f"create schema {DATABASE}.{schema}")
    for ddl in call_macro("_icecore_emit_procedures_ddl", schema=schema):
        cur.execute(ddl)
    cur.execute(f"create procedure {_qualified(schema, PROBE)}() returns varchar language sql execute as owner as "
                "$$ begin return 'ok'; end; $$")


def _signatures(schema: str):
    return [f"{_qualified(schema, 'ICECORE_EMIT')}(ARRAY)", f"{_qualified(schema, 'ICECORE_EMIT_RUN')}(ARRAY)",
            f"{_qualified(schema, PROBE)}()"]


def _counts(sf, schema: str, since) -> dict:
    """Records of one case's schema by <executable>.<record type>. The Python runtime adds log lines of its own, counted as
    LOG_OTHER; LOG is the icecore message only. A SPAN
    is the procedure's own span (`run` for Python); ICECORE_SPAN is an explicit span written by ICECORE_EMIT_RUN."""
    cur = sf.cursor(DictCursor)
    rows = cur.execute(
        f"select split_part(resource_attributes:\"snow.executable.name\"::string, '(', 1) as exe, "
        f"case when record_type = 'LOG' and value::string <> %s then 'LOG_OTHER' "
        f"when record_type = 'SPAN' and record:name::string like 'icecore.lv.span%%' then 'ICECORE_SPAN' "
        f"when record_type = 'SPAN_EVENT' and record:name::string = 'icecore.lv.inner' then 'ICECORE_RUN_SPAN_EVENT' "
        f"else record_type end as rt, count(*) as n "
        f"from {EVENT_TABLE} where timestamp >= %s and resource_attributes:\"snow.schema.name\"::string = %s "
        f"group by 1, 2", (LOG_MESSAGE, since, schema)).fetchall()
    return {f"{r['EXE']}.{r['RT']}": r["N"] for r in rows}


# What a case lands, keyed by <executable>.<record type>.
FULL = {"ICECORE_EMIT.SPAN_EVENT": 1, "ICECORE_EMIT.SPAN": 1, "ICECORE_EMIT_RUN.SPAN": 1, "ICECORE_EMIT_RUN.ICECORE_SPAN": 2,
        "ICECORE_EMIT_RUN.ICECORE_RUN_SPAN_EVENT": 1, "ICECORE_EMIT_RUN.LOG": 1,
        f"{PROBE}.SPAN": 1}


@pytest.mark.sandbox
def test_levels_matrix_on_run_id_schemas(sf, run_id):
    cur = sf.cursor()
    cur.execute(f"show parameters like '%LEVEL' in database {DATABASE}")
    db_levels = {r[0]: r[1] for r in cur.fetchall()}
    assert db_levels["LOG_LEVEL"] == "INFO" and db_levels["TRACE_LEVEL"] == "ALWAYS", db_levels  # never altered here

    schemas = {case: f"GATE1_LV_{run_id[:12]}_{i}".upper() for i, case in enumerate(CASES)}
    since = cur.execute("select sysdate()").fetchone()[0]
    try:
        for case, (schema_levels, proc_levels) in CASES.items():
            schema = schemas[case]
            _create(cur, schema)
            if schema_levels:
                cur.execute(f"alter schema {DATABASE}.{schema} set {schema_levels}")
            if proc_levels:
                for signature in _signatures(schema):
                    cur.execute(f"alter procedure {signature} set {proc_levels}")
            assert cur.execute(f"call {_qualified(schema, 'ICECORE_EMIT')}(parse_json(%s))",
                               (json.dumps(EMIT_ARGS),)).fetchone()[0] == "ok 1"
            assert cur.execute(f"call {_qualified(schema, 'ICECORE_EMIT_RUN')}(parse_json(%s))",
                               (json.dumps(_run_args()),)).fetchone()[0] == "ok spans=2 logs=1"
            assert cur.execute(f"call {_qualified(schema, PROBE)}()").fetchone()[0] == "ok"

        # the control must land; then wait so that absence is a measurement, not a race with ingestion
        deadline = time.monotonic() + TIMEOUT_S
        while any(_counts(sf, schemas["inherit"], since).get(k, 0) < n for k, n in FULL.items()):
            assert time.monotonic() < deadline, _counts(sf, schemas["inherit"], since)
            time.sleep(5)
        time.sleep(SETTLE_S)
        found = {case: _counts(sf, schema, since) for case, schema in schemas.items()}
        landed = {c: {k: n for k, n in v.items() if not k.endswith(".LOG_OTHER")} for c, v in found.items()}
        record_finding("gate1_followups", "levels_matrix", {
            "run_id": run_id, "database_levels": db_levels, "measured": True, "settle_s": SETTLE_S,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "cases": {c: {"schema": CASES[c][0], "procedure": CASES[c][1], "landed": landed[c], "python_runtime_logs": sum(n for k, n in found[c].items() if k.endswith(".LOG_OTHER"))} for c in CASES}})

        assert landed["inherit"] == FULL, found
        assert landed["schema_off"] == {}, found                      # the schema's value overrides the database's
        assert landed["schema_off_proc_info_always"] == FULL, found    # the procedure's overrides the schema's
        assert landed["schema_info_always"] == FULL, found
        # the probe adds no event: it lands a span only when the effective TRACE_LEVEL is ALWAYS, so a missing probe span
        # proves ON_EVENT took over from the database's ALWAYS
        assert f"{PROBE}.SPAN" not in landed["schema_info_on_event"], found
        assert landed["schema_info_on_event"].get("ICECORE_EMIT.SPAN_EVENT") == 1, found
        assert landed["schema_info_on_event"].get("ICECORE_EMIT_RUN.LOG") == 1, found
    finally:
        for schema in schemas.values():
            cur.execute(f"drop schema if exists {DATABASE}.{schema}")
