"""UC1 writer: icecore.emit / icecore.emit_events (macros/telemetry/emit.sql) and the write-path contract
icecore.create_emit_procedures (macros/setup/procedures.sql), Gate 1 option B (spikes/GATE1.md).

emit renders one SPAN_EVENT per record, named `icecore.<kind>`, with base_attributes() and the caller's
attributes, caps each record at payload_cap_kb (default 64, clamped at 256) with a truncation flag, chunks
at most 100 events and 512 KB per CALL, and CALLs <target db>.<obs schema>.ICECORE_EMIT inside the guard
block, so a missing procedure or grant logs a warning and never fails the run.

Offline tests render through the wrappers in integration_tests/macros/test_support/it_emit.sql.
Sandbox tests create both procedures in their own schema ICECORE_OBS_<id> (passed as the obs_schema var),
read the event table from a window opened on Snowflake's clock, and drop the schema at the end.
"""
import calendar
import json
import os
import re
import uuid
from pathlib import Path

import pytest

from helpers.events import wait_for_events
from helpers.macros import IT_DIR, run_macro, run_macro_with_logs

REPO = IT_DIR.parent
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
WEIRD = "it's a \\ test\nnew line é ü 漢\ttab \"double\" $$ end of block? $$"
KB = 1024


def with_vars(icecore_vars: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": icecore_vars})]


# --- offline: rendering only, no connection ------------------------------------------------------

def test_obs_schema_defaults_to_icecore_obs():
    assert run_macro("it_obs_schema", {}) == {"name": "ICECORE_OBS", "qualified": f'"{DATABASE}"."ICECORE_OBS"'}


def test_obs_schema_follows_the_var():
    out = run_macro("it_obs_schema", {}, with_vars({"obs_schema": "icecore_obs_ci"}))
    assert out == {"name": "ICECORE_OBS_CI", "qualified": f'"{DATABASE}"."ICECORE_OBS_CI"'}


def test_package_declares_the_obs_schema_var():
    import yaml
    assert yaml.safe_load(open(REPO / "dbt_project.yml"))["vars"]["icecore"]["obs_schema"] == "ICECORE_OBS"


def test_procedure_ddl_is_the_gate1_contract():
    emit, emit_run, score = run_macro("it_procedure_ddl", {})
    schema = f'"{DATABASE}"."ICECORE_OBS"'
    assert emit.startswith(f'CREATE OR REPLACE PROCEDURE {schema}."ICECORE_EMIT"(events ARRAY)')
    assert "LANGUAGE SQL" in emit and "EXECUTE AS OWNER" in emit
    assert "SYSTEM$ADD_EVENT(:nm, :a)" in emit
    assert "> 100" in emit  # refuses more than 100 events per CALL
    assert emit_run.startswith(f'CREATE OR REPLACE PROCEDURE {schema}."ICECORE_EMIT_RUN"(spans ARRAY)')
    assert "LANGUAGE PYTHON" in emit_run and "EXECUTE AS OWNER" in emit_run
    assert "PACKAGES = ('snowflake-snowpark-python', 'opentelemetry-api==1.38.0')" in emit_run
    assert "snowflake-telemetry-python" not in emit_run
    assert 'trace.get_tracer("icecore")' in emit_run and 'logging.getLogger("icecore")' in emit_run
    assert score.startswith(f'CREATE OR REPLACE PROCEDURE {schema}."ICECORE_SCORE"(db STRING, sch STRING, model STRING, series STRING, points ARRAY')
    assert "RETURNS VARIANT" in score and "LANGUAGE SQL" in score and "EXECUTE AS OWNER" in score and "COPY GRANTS" in score


def test_records_carry_prefix_base_attributes_and_message():
    out = run_macro("it_emit_render", {"events": [
        {"kind": "metric", "message": WEIRD, "attributes": {"dbt.metric.value": 1200, "icecore.flag": True}},
        {"kind": "icecore.anomaly", "level": "warn"},
    ]})
    first, second = out["records"]
    assert first["name"] == "icecore.metric"
    assert second["name"] == "icecore.anomaly"
    a = first["attrs"]
    assert a["icecore.message"] == WEIRD
    assert a["icecore.level"] == "info" and second["attrs"]["icecore.level"] == "warn"
    assert a["dbt.metric.value"] == 1200 and a["icecore.flag"] is True
    assert a["dbt.invocation_id"] and a["dbt.command"] == "run-operation"
    assert a["icecore.schema_version"] == 1
    assert "icecore.truncated" not in a


def test_over_cap_record_is_truncated_and_flagged():
    big = "x" * (100 * KB)
    out = run_macro("it_emit_render", {"events": [{"kind": "sample", "message": "m", "attributes": {"dbt.sample.rows": big}}]})
    rec = out["records"][0]
    assert len(json.dumps(rec["attrs"])) <= 64 * KB
    assert rec["attrs"]["icecore.truncated"] is True
    assert rec["attrs"]["icecore.truncated_keys"] == "dbt.sample.rows"
    assert big.startswith(rec["attrs"]["dbt.sample.rows"]) and len(rec["attrs"]["dbt.sample.rows"]) > 60 * KB


@pytest.mark.parametrize("cap,limit", [(1024, 256), (128, 128), (0, 1)])
def test_payload_cap_is_clamped(cap, limit):
    big = "y" * (300 * KB)
    out = run_macro("it_emit_render", {"events": [{"kind": "sample", "attributes": {"v": big}}]},
                    with_vars({"payload_cap_kb": cap}))
    assert out["cap_bytes"] == limit * KB
    assert len(json.dumps(out["records"][0]["attrs"])) <= limit * KB


def test_chunks_hold_at_most_100_events_and_512_kb():
    small = [{"kind": "metric", "attributes": {"i": i}} for i in range(250)]
    assert run_macro("it_emit_render", {"events": small})["chunk_sizes"] == [100, 100, 50]
    big = [{"kind": "metric", "attributes": {"i": i, "v": "z" * (60 * KB)}} for i in range(20)]
    out = run_macro("it_emit_render", {"events": big})
    assert sum(out["chunk_sizes"]) == 20
    assert all(b <= 512 * KB for b in out["chunk_bytes"])
    assert len(out["chunk_sizes"]) >= 3


def test_call_is_guarded_and_escaped():
    out = run_macro("it_emit_render", {"events": [{"kind": "metric", "message": WEIRD}]})
    sql = out["sql"]
    assert sql.startswith("EXECUTE IMMEDIATE $$")
    assert f'CALL "{DATABASE}"."ICECORE_OBS"."ICECORE_EMIT"(PARSE_JSON(\'' in sql
    assert "EXCEPTION" in sql and "WHEN OTHER THEN RETURN 'icecore-error '" in sql
    inner = sql[len("EXECUTE IMMEDIATE $$"):sql.rstrip().rindex("$$")]
    assert "$$" not in inner  # the payload can never close the block
    assert "\n" not in inner[inner.index("PARSE_JSON("):inner.index(") INTO")]


def test_package_source_has_no_insert():
    pattern = re.compile(r"\b(insert\s+(into|overwrite)|merge\s+into|create\s+(or\s+replace\s+)?"
                         r"(temporary\s+|transient\s+|hybrid\s+)?(table|stage))\b", re.I)
    offenders = []
    for path in list((REPO / "macros").rglob("*.sql")) + list((REPO / "tests").rglob("*.sql")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, offenders


# --- sandbox --------------------------------------------------------------------------------------

def snowflake_since(sf, minutes: int = 5):
    """A window start on Snowflake's clock, in UTC like the event table's TIMESTAMP (the laptop runs ~10 s ahead)."""
    return sf.cursor().execute(
        f"select dateadd(minute, -{minutes}, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]


@pytest.fixture(scope="module")
def obs(dbt, sf):
    """Both procedures, created by icecore.create_emit_procedures in a schema of their own; dropped afterwards."""
    schema = f"ICECORE_OBS_{uuid.uuid4().hex}".upper()
    sf.cursor().execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        _, logs = run_macro_with_logs("it_create_emit_procedures", {}, with_vars({"obs_schema": schema}))
        yield {"schema": schema, "vars": with_vars({"obs_schema": schema}), "logs": logs}
    finally:
        sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{schema}"')


def ours(obs) -> str:
    """The base filter of GATE1.md: database, schema and executable name, then the run tag."""
    return ("resource_attributes:\"snow.database.name\"::string = %(db)s"
            " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
            " and startswith(resource_attributes:\"snow.executable.name\"::string, 'ICECORE_EMIT')"
            " and record_attributes:\"it.run_id\"::string = %(tag)s")


def epoch_ms(ntz) -> int:
    """Milliseconds since the epoch of a TIMESTAMP_NTZ in UTC (naive datetime)."""
    return calendar.timegm(ntz.utctimetuple()) * 1000 + ntz.microsecond // 1000


def params(obs, tag: str) -> dict:
    return {"db": DATABASE, "schema": obs["schema"], "tag": tag}


@pytest.mark.sandbox
def test_create_emit_procedures_creates_both(sf, obs):
    rows = sf.cursor().execute(f'show user procedures in schema "{DATABASE}"."{obs["schema"]}"').fetchall()
    names = sorted(r[1] for r in rows)
    assert names == ["ICECORE_EMIT", "ICECORE_EMIT_RUN"]


@pytest.mark.sandbox
def test_emit_lands_with_attributes_and_message_round_trips(sf, obs, run_id):
    since = snowflake_since(sf)
    out = run_macro("it_emit", {"kind": "it_emit", "message": WEIRD,
                                "attributes": {"it.run_id": run_id, "dbt.metric.value": 1200, "icecore.flag": True}},
                    obs["vars"])
    assert out["calls"] == 1 and out["statuses"] == ["ok 1"], out
    rows = wait_for_events(sf, ours(obs), params(obs, run_id), since=since)
    assert len(rows) == 1
    row = rows[0]
    a = row["record_attributes"]
    assert row["record_type"] == "SPAN_EVENT"
    assert row["record"]["name"] == "icecore.it_emit"
    assert row["scope"]["name"] == "global"
    assert a["icecore.message"] == WEIRD
    assert a["dbt.metric.value"] == 1200 and a["icecore.flag"] is True
    assert a["dbt.invocation_id"] and a["icecore.schema_version"] == 1
    assert row["resource_attributes"]["snow.executable.type"] == "PROCEDURE"


@pytest.mark.sandbox
def test_over_cap_payload_is_truncated_flagged_and_the_call_succeeds(sf, obs, run_id):
    since = snowflake_since(sf)
    big = "x" * (100 * KB)
    out = run_macro("it_emit", {"kind": "it_emit", "message": "big",
                                "attributes": {"it.run_id": run_id, "dbt.sample.rows": big}}, obs["vars"])
    assert out["statuses"] == ["ok 1"], out
    a = wait_for_events(sf, ours(obs), params(obs, run_id), since=since)[0]["record_attributes"]
    assert a["icecore.truncated"] is True and a["icecore.truncated_keys"] == "dbt.sample.rows"
    assert 60 * KB < len(a["dbt.sample.rows"]) < 64 * KB and big.startswith(a["dbt.sample.rows"])


@pytest.mark.sandbox
def test_levels_off_does_not_fail_the_run(sf, obs, run_id):
    since = snowflake_since(sf)
    out = run_macro("it_emit_levels_off", {"kind": "it_levels_off", "attributes": {"it.run_id": run_id}}, obs["vars"])
    assert out["session"] == {"LOG_LEVEL": "OFF", "TRACE_LEVEL": "OFF"}
    assert out["emit"]["statuses"] == ["ok 1"], out
    # Not required by the story: GATE1.md says the database's levels win, so the record still lands.
    assert len(wait_for_events(sf, ours(obs), params(obs, run_id), since=since)) == 1


@pytest.mark.sandbox
def test_missing_procedure_logs_a_warning_and_never_fails(dbt, run_id):
    missing = with_vars({"obs_schema": f"ICECORE_OBS_MISSING_{run_id}"})
    out, logs = run_macro_with_logs("it_emit", {"kind": "it_missing", "attributes": {"it.run_id": run_id}}, missing)
    assert out["calls"] == 1 and out["statuses"][0].startswith("icecore-error"), out
    assert any(line.startswith("icecore: warning: telemetry not written") for line in logs), logs


@pytest.mark.sandbox
def test_150_events_are_chunked_and_all_land(sf, obs, run_id):
    since = snowflake_since(sf)
    events = [{"kind": "it_batch", "attributes": {"it.run_id": run_id, "it.seq": i}} for i in range(1, 151)]
    out = run_macro("it_emit_events", {"events": events}, obs["vars"])
    assert out["calls"] == 2 and out["statuses"] == ["ok 100", "ok 50"], out
    rows = wait_for_events(sf, ours(obs), params(obs, run_id), min_rows=150, since=since, settle_s=10)
    assert sorted(r["record_attributes"]["it.seq"] for r in rows) == list(range(1, 151))


@pytest.mark.sandbox
def test_emit_run_procedure_writes_spans_and_logs_with_scope_icecore(sf, obs, run_id):
    since = snowflake_since(sf, minutes=10)
    now_ns = int(sf.cursor().execute("select date_part(epoch_nanosecond, current_timestamp())").fetchone()[0])
    start_ns, end_ns = now_ns - 120 * 10**9, now_ns - 118 * 10**9
    items = [
        {"name": "icecore.node", "start_ns": start_ns, "end_ns": end_ns,
         "attrs": {"it.run_id": run_id, "dbt.result.rows_affected": 1200},
         "events": [{"name": "icecore.query", "ts_ns": start_ns, "attrs": {"it.run_id": run_id, "snow.query.id": "q1"}}]},
        {"type": "log", "level": "warn", "message": WEIRD, "attrs": {"it.run_id": run_id, "dbt.result.status": "warn"}},
    ]
    out = run_macro("it_emit_run", {"items": items}, obs["vars"])
    assert out == "ok spans=1 logs=1", out
    rows = wait_for_events(sf, ours(obs), params(obs, run_id), min_rows=3, since=since, settle_s=5)
    by_type = {r["record_type"]: r for r in rows}
    assert set(by_type) == {"SPAN", "SPAN_EVENT", "LOG"}
    assert all(r["scope"]["name"] == "icecore" for r in rows)
    span = by_type["SPAN"]
    assert span["record_attributes"]["dbt.result.rows_affected"] == 1200
    assert epoch_ms(span["start_timestamp"]) == start_ns // 10**6
    assert epoch_ms(span["timestamp"]) == end_ns // 10**6
    log = by_type["LOG"]
    assert log["value"] == WEIRD and log["record"]["severity_text"] == "WARN"
    assert log["record_attributes"]["dbt.result.status"] == "warn"
