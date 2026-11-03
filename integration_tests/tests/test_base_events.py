"""UC5 base event filter: icecore.base_events() and icecore.resolve_event_table().

Offline tests render the SQL (no connection). Sandbox tests emit fixture records from run_id-named
procedures in a run_id-named schema, with the S1 probe macros (integration_tests/macros/spikes/s1_emit_proc.sql)
and the foreign ones in integration_tests/macros/test_support/it_base_events.sql, then read them back
through a view built on base_events:

| case                | procedure                               | record                       | expected |
|---------------------|-----------------------------------------|------------------------------|----------|
| kept_emit           | <schema>.ICECORE_EMIT_<id> (SQL)        | SPAN_EVENT `icecore.probe`   | returned |
| kept_emit_run       | <schema>.ICECORE_EMIT_RUN_<id> (Python) | SPAN, SCOPE `icecore`        | returned |
| foreign_name        | <schema>.ICECORE_EMIT_<id> (SQL)        | SPAN_EVENT `other.probe`     | excluded |
| foreign_scope       | <schema>.ICECORE_EMIT_RUN_OTHER_<id>    | SPAN, SCOPE `other_library`  | excluded |
| foreign_executable  | <schema>.OTHER_EMIT_<id> (SQL)          | SPAN_EVENT `icecore.probe`   | excluded |
| foreign_schema      | SPIKES.ICECORE_EMIT_<id> (SQL)          | SPAN_EVENT `icecore.probe`   | excluded |
"""
import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import MacroFailed, call_macro, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SPIKES = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
TABLE = "ICECORE_SANDBOX.TELEMETRY.EVENTS"

NUMERIC = {
    "dbt.result.rows_affected": 1200,
    "dbt.result.execution_time_s": 4.25,
    "dbt.test.failures": 3,
    "dbt.metric.value": 12.5,
    "dbt.anomaly.is_anomaly": True,
}
# column -> (expected Snowflake type prefix, expected Python value)
TYPED = {
    "RESULT_ROWS_AFFECTED": ("NUMBER(38,0)", 1200),
    "RESULT_EXECUTION_TIME_S": ("FLOAT", 4.25),
    "TEST_FAILURES": ("NUMBER(38,0)", 3),
    "METRIC_VALUE": ("FLOAT", 12.5),
    "ANOMALY_IS_ANOMALY": ("BOOLEAN", True),
}
KEPT = {"kept_emit", "kept_emit_run"}
FOREIGN = {"foreign_name", "foreign_scope", "foreign_executable", "foreign_schema"}


def render(**kwargs) -> str:
    return call_macro("base_events", **kwargs)


def where_clause(sql: str) -> str:
    return sql[re.search(r"\bwhere\b", sql, re.I).start():]


# --- offline: rendering only, no connection ------------------------------------------------------

def test_predicates_come_first_in_order():
    sql = render(event_table=TABLE, database="icecore_sandbox")
    where = where_clause(sql)
    order = [
        "RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG')",
        """RESOURCE_ATTRIBUTES:"snow.database.name"::string = 'ICECORE_SANDBOX'""",
        """RESOURCE_ATTRIBUTES:"snow.schema.name"::string = 'ICECORE_OBS'""",
        """RESOURCE_ATTRIBUTES:"snow.executable.name"::string like 'ICECORE_EMIT%'""",
        "SCOPE:name::string = 'icecore'",
        "RECORD:name::string like 'icecore.%'",
    ]
    positions = [where.find(p) for p in order]
    assert all(p >= 0 for p in positions), dict(zip(order, positions))
    assert positions == sorted(positions), dict(zip(order, positions))
    assert f"from {TABLE}" in sql


def test_obs_schema_comes_from_the_icecore_var():
    sql = run_macro("it_call", {"name": "base_events", "kwargs": {"event_table": TABLE}},
                    extra=["--vars", json.dumps({"icecore": {"obs_schema": "team_obs"}})])
    assert """"snow.schema.name"::string = 'TEAM_OBS'""" in sql
    # an explicit argument wins over the var
    sql = render(event_table=TABLE, obs_schema="Other_Obs")
    assert """"snow.schema.name"::string = 'OTHER_OBS'""" in sql


def test_database_defaults_to_the_target():
    sql = render(event_table=TABLE)
    assert f""""snow.database.name"::string = '{DATABASE.upper()}'""" in sql


def test_literals_are_escaped():
    sql = render(event_table=TABLE, database="o'brien", obs_schema="x_y")
    # a database that is not a plain identifier is kept as written (quoted names keep their case)
    assert "= 'o\\'brien'" in sql
    assert "= 'X_Y'" in sql
    # the obs schema is an identifier: a backslash or a quote in it is refused at compile time
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        render(event_table=TABLE, database="D", obs_schema="x\\y")


def test_numeric_attributes_are_typed():
    sql = render(event_table=TABLE)
    assert """try_to_number(RECORD_ATTRIBUTES:"dbt.result.rows_affected"::string) as result_rows_affected""" in sql
    assert """try_to_double(RECORD_ATTRIBUTES:"dbt.result.execution_time_s"::string) as result_execution_time_s""" in sql
    assert """try_to_double(RECORD_ATTRIBUTES:"dbt.metric.value"::string) as metric_value""" in sql
    assert """try_to_boolean(RECORD_ATTRIBUTES:"dbt.anomaly.is_anomaly"::string) as anomaly_is_anomaly""" in sql
    for column in ("event_at", "started_at", "observed_at", "invocation_id", "attributes"):
        assert re.search(rf"\bas {column}\b", sql), column


def test_package_docs_have_no_jinja():
    """dbt renders descriptions as Jinja at parse time, where `icecore` is undefined: one `{{ ... }}` in a package
    yml breaks parsing for every project that installs icecore."""
    repo = Path(__file__).resolve().parents[2]
    offenders = [f"{p.relative_to(repo)}:{n}" for p in (repo / "macros").rglob("*.yml")
                 for n, line in enumerate(p.read_text().splitlines(), 1) if "{{" in line or "{%" in line]
    assert not offenders, offenders


@pytest.mark.parametrize("value,expected", [
    ("ICECORE_SANDBOX.TELEMETRY.EVENTS", "ICECORE_SANDBOX.TELEMETRY.EVENTS"),
    ("SNOWFLAKE.TELEMETRY.EVENTS", "SNOWFLAKE.TELEMETRY.EVENTS_VIEW"),
    ("snowflake.telemetry.events", "SNOWFLAKE.TELEMETRY.EVENTS_VIEW"),
    ("", None),
    (None, None),
])
def test_event_table_to_read(value, expected):
    assert call_macro("event_table_to_read", event_table=value) == expected


# --- sandbox --------------------------------------------------------------------------------------

def _now(sf):
    return sf.cursor().execute(
        "select current_timestamp(), date_part(epoch_nanosecond, current_timestamp())").fetchone()


def _events(run_id, case, name="icecore.probe", extra=None):
    return [{"name": name, "attrs": {"icecore.run_id": run_id, "icecore.case": case, **(extra or {})}}]


def _spans(run_id, case, start_ns, extra=None):
    return [{"name": "icecore.node", "start_ns": start_ns, "end_ns": start_ns + 1_000_000_000,
             "attrs": {"icecore.run_id": run_id, "icecore.case": case, **(extra or {})}}]


@pytest.fixture(scope="module")
def fixture_records(dbt, sf):
    """Emit the six fixture records once for the module; yield (run_id, schema, since, view)."""
    run_id = uuid.uuid4().hex
    schema = f"UC5_BE_{run_id}".upper()
    rid = run_id.upper()
    cur = sf.cursor()
    cur.execute(f"create schema {DATABASE}.{schema}")
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("SNOWFLAKE_SCHEMA", schema)  # the S1 probe macros create in target.schema
            run_macro("s1_emit_proc_create", {"run_id": run_id})
            run_macro("s1_emit_run_proc_create", {"run_id": run_id})
        run_macro("s1_emit_proc_create", {"run_id": run_id})  # same name, in SPIKES
        run_macro("it_be_foreign_procs", {"schema": schema, "run_id": run_id})

        since, now_ns = _now(sf)
        start_ns = int(now_ns) - 60_000_000_000
        calls = [
            {"proc": f"{DATABASE}.{schema}.ICECORE_EMIT_{rid}",
             "events": _events(run_id, "kept_emit", extra=NUMERIC) + _events(run_id, "foreign_name", "other.probe")},
            {"proc": f"{DATABASE}.{schema}.ICECORE_EMIT_RUN_{rid}", "events": _spans(run_id, "kept_emit_run", start_ns, NUMERIC)},
            {"proc": f"{DATABASE}.{schema}.ICECORE_EMIT_RUN_OTHER_{rid}", "events": _spans(run_id, "foreign_scope", start_ns)},
            {"proc": f"{DATABASE}.{schema}.OTHER_EMIT_{rid}", "events": _events(run_id, "foreign_executable")},
            {"proc": f"{DATABASE}.{SPIKES}.ICECORE_EMIT_{rid}", "events": _events(run_id, "foreign_schema")},
        ]
        statuses = run_macro("it_be_emit", {"calls": calls})
        assert all(str(s).startswith("ok") for s in statuses), statuses

        # all six land in the raw table first, so an absence below is the filter's doing, not lag
        raw = wait_for_events(sf, "record_attributes:\"icecore.run_id\" = %(r)s", {"r": run_id}, min_rows=6,
                              timeout_s=300, since=since - dt.timedelta(minutes=2),
                              settle_s=10)
        assert {r["record_attributes"]["icecore.case"] for r in raw} == KEPT | FOREIGN, raw

        view = f"{DATABASE}.{schema}.BASE_EVENTS"
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        yield run_id, schema, since, view
    finally:
        cur.execute(f"drop schema if exists {DATABASE}.{schema} cascade")
        cur.execute(f"drop procedure if exists {DATABASE}.{SPIKES}.ICECORE_EMIT_{rid}(ARRAY)")


def _read(sf, view, run_id, since):
    return sf.cursor(DictCursor).execute(
        f"select *, attributes:\"icecore.case\"::string as case_name from {view} "
        "where attributes:\"icecore.run_id\"::string = %(r)s and event_at >= dateadd(minute, -5, %(s)s)",
        {"r": run_id, "s": since}).fetchall()


@pytest.mark.sandbox
def test_resolves_the_sandbox_event_table(dbt):
    assert call_macro("resolve_event_table") == TABLE


@pytest.mark.sandbox
def test_other_scope_and_foreign_records_are_excluded(sf, fixture_records):
    run_id, _, since, view = fixture_records
    rows = _read(sf, view, run_id, since)
    assert sorted(r["CASE_NAME"] for r in rows) == sorted(KEPT), rows
    by_case = {r["CASE_NAME"]: r for r in rows}
    assert by_case["kept_emit"]["RECORD_TYPE"] == "SPAN_EVENT"
    assert by_case["kept_emit"]["SCOPE_NAME"] == "global"
    assert by_case["kept_emit_run"]["RECORD_TYPE"] == "SPAN"
    assert by_case["kept_emit_run"]["SCOPE_NAME"] == "icecore"


@pytest.mark.sandbox
def test_numeric_attributes_are_numbers(sf, fixture_records):
    run_id, _, since, view = fixture_records
    types = {r[0]: r[1] for r in sf.cursor().execute(f"describe view {view}").fetchall()}
    rows = _read(sf, view, run_id, since)
    assert len(rows) == 2
    for column, (sf_type, value) in TYPED.items():
        assert types[column].startswith(sf_type), (column, types[column])
        for row in rows:
            assert row[column] == value and type(row[column]) is type(value), (column, row[column])


@pytest.mark.sandbox
def test_explain_applies_record_type_and_scope_first(sf, fixture_records):
    _, _, _, view = fixture_records
    plan = json.loads(sf.cursor().execute(f"explain using json select * from {view}").fetchone()[0])
    ops = {op["id"]: op for op in plan["Operations"][0]}
    scans = [op for op in ops.values() if op["operation"] == "TableScan"]
    assert len(scans) == 1 and scans[0]["objects"] == [TABLE], scans
    first = ops[scans[0]["parentOperators"][0]]
    assert first["operation"] == "Filter", first
    expr = first["expressions"][0]
    assert expr.startswith("(EVENTS.RECORD_TYPE IN 'SPAN' IN 'SPAN_EVENT' IN 'LOG')"), expr
    scope = expr.find("GET_PATH(EVENTS.SCOPE, 'name')")
    assert 0 < scope, expr
    for key in ("snow.database.name", "snow.schema.name", "snow.executable.name"):
        assert 0 < expr.find(key) < scope, (key, expr)
