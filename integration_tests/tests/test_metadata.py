"""UC2 zero-scan readers: icecore.get_last_change_time, icecore.get_row_count_metadata (total) and
icecore.get_row_count_changes (rows added over CHANGES).

The readers are called through the `it_metadata` / `it_quoted_name` wrappers in
integration_tests/macros/test_support/it_metadata.sql, which log their results as `icecore_it_result:<json>`.
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest
from dbt.cli.main import dbtRunner

from conftest import IT_DIR
from test_offline import PLACEHOLDER_ENV

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
N_ROWS = 100
WAIT_S = 60
RESULT_PREFIX = "icecore_it_result:"


def run_macro_with_logs(macro: str, args: dict, extra: list[str] | None = None):
    """Run a macro through dbt run-operation; return (JSON value logged with RESULT_PREFIX, other icecore log lines)."""
    seen, logs = [], []

    def capture(event):
        msg = getattr(event.data, "msg", "")
        if event.info.name != "JinjaLogInfo":
            return
        if msg.startswith(RESULT_PREFIX):
            seen.append(json.loads(msg[len(RESULT_PREFIX):]))
        elif msg.startswith("icecore:"):
            logs.append(msg)

    res = dbtRunner(callbacks=[capture]).invoke(
        ["run-operation", macro, "--args", json.dumps(args), "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]
        + (extra or [])
    )
    detail = res.exception or [getattr(r, "message", r) for r in getattr(res.result, "results", [])]
    assert res.success, f"{macro} failed: {detail}"
    return (seen[-1] if seen else None), logs


def run_macro(macro: str, args: dict, extra: list[str] | None = None):
    return run_macro_with_logs(macro, args, extra)[0]


def quoted(*parts: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def rel(identifier: str, quoted_identifier: bool = False) -> dict:
    return {"database": DATABASE, "schema": SCHEMA, "identifier": identifier, "quoted": quoted_identifier}


def utc_now(sf) -> str:
    return sf.cursor().execute("""select to_varchar(sysdate(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"')""").fetchone()[0]


def iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def write_rows(sf, relation: str, n: int = N_ROWS) -> None:
    sf.cursor().execute(f"insert into {relation} (id, note) select seq4(), 'uc2' from table(generator(rowcount => {n}))")


# --- offline: rendering only, no connection ------------------------------------------------------

@pytest.mark.parametrize("identifier,quoted_identifier,expected", [
    ("fct_orders", False, '"ICECORE_SANDBOX"."SPIKES"."FCT_ORDERS"'),
    ('Mixed Case "Orders"', True, '"ICECORE_SANDBOX"."SPIKES"."Mixed Case ""Orders"""'),
])
def test_quoted_name_follows_quote_policy(monkeypatch, identifier, quoted_identifier, expected):
    for key, value in PLACEHOLDER_ENV.items():
        monkeypatch.setenv(key, os.environ.get(key, value))
    assert dbtRunner().invoke(["deps", "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]).success
    args = {"database": "icecore_sandbox", "schema": "spikes", "identifier": identifier, "quoted": quoted_identifier}
    assert run_macro("it_quoted_name", args) == expected


# --- sandbox --------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_readers_on_standard_and_transient_tables(dbt, sf, run_id):
    names = {"standard": f"UC2_STD_{run_id}".upper(), "transient": f"UC2_TRN_{run_id}".upper()}
    cur = sf.cursor()
    cur.execute(f"create table {quoted(DATABASE, SCHEMA, names['standard'])} (id number, note varchar)")
    cur.execute(f"create transient table {quoted(DATABASE, SCHEMA, names['transient'])} (id number, note varchar)")
    try:
        written_at = utc_now(sf)
        for name in names.values():
            write_rows(sf, quoted(DATABASE, SCHEMA, name))
        time.sleep(WAIT_S)
        # unquoted, lower-case identifiers, as a dbt ref() on Snowflake hands them over
        out = run_macro("it_metadata", {"relations": [rel(n.lower()) for n in names.values()]})
        for kind, name in names.items():
            got = out[name.lower()]
            assert got["row_count"] == N_ROWS, (kind, got)
            assert got["last_change"] is not None and iso(got["last_change"]) >= iso(written_at), (kind, got, written_at)
    finally:
        for name in names.values():
            cur.execute(f"drop table if exists {quoted(DATABASE, SCHEMA, name)}")


@pytest.mark.sandbox
def test_readers_find_a_quoted_mixed_case_table(dbt, sf, run_id):
    name = f'Mixed Case "Orders" {run_id}'
    target = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create table {target} (id number, note varchar)")
    try:
        written_at = utc_now(sf)
        write_rows(sf, target)
        deadline = time.monotonic() + WAIT_S
        got = run_macro("it_metadata", {"relations": [rel(name, True)]})[name]
        while got["row_count"] != N_ROWS and time.monotonic() < deadline:
            time.sleep(5)
            got = run_macro("it_metadata", {"relations": [rel(name, True)]})[name]
        assert got["row_count"] == N_ROWS
        assert got["last_change"] is not None and iso(got["last_change"]) >= iso(written_at)
    finally:
        sf.cursor().execute(f"drop table if exists {target}")


@pytest.mark.sandbox
def test_rows_added_over_changes_and_the_fallback_switch(dbt, sf, run_id):
    name = f"UC2_FALLBACK_{run_id}".upper()
    target = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create transient table {target} (id number, note varchar) change_tracking = true")
    try:
        time.sleep(2)  # CHANGES needs an AT time after change tracking started
        since = utc_now(sf)
        write_rows(sf, target)
        default = run_macro("it_metadata", {"relations": [rel(name.lower())], "since": since})[name.lower()]
        assert default["row_count"] == N_ROWS and default["rows_added"] == N_ROWS
        # transient declared a fallback kind: no metadata total, the delta still comes from CHANGES
        switch = ["--vars", json.dumps({"icecore": {"metadata_fallback_kinds": ["transient"]}})]
        got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())], "since": since}, switch)
        assert got[name.lower()]["row_count"] is None and got[name.lower()]["rows_added"] == N_ROWS
        assert any("use get_row_count_changes" in m for m in logs), logs
    finally:
        sf.cursor().execute(f"drop table if exists {target}")


@pytest.mark.sandbox
def test_changes_without_change_tracking_returns_none_without_failing(dbt, sf, run_id):
    name = f"UC2_NOTRACK_{run_id}".upper()
    target = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create transient table {target} (id number, note varchar)")
    try:
        since = utc_now(sf)
        write_rows(sf, target)
        got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())], "since": since})
        assert got[name.lower()]["rows_added"] is None
        assert got[name.lower()]["row_count"] == N_ROWS
        assert any("Change tracking is not enabled" in m for m in logs), logs
    finally:
        sf.cursor().execute(f"drop table if exists {target}")


@pytest.mark.sandbox
def test_view_is_unsupported_for_zero_scan_volume(dbt, sf, run_id):
    table = quoted(DATABASE, SCHEMA, f"UC2_VBASE_{run_id}".upper())
    name = f"UC2_VIEW_{run_id}".upper()
    view = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create table {table} (id number, note varchar)")
    sf.cursor().execute(f"create view {view} as select id, note from {table}")
    try:
        got, logs = run_macro_with_logs("it_metadata", {"relations": [rel(name.lower())]})
        assert got[name.lower()]["row_count"] is None
        unsupported = [m for m in logs if "not supported" in m]
        assert len(unsupported) == 1 and "is a view" in unsupported[0], logs
    finally:
        sf.cursor().execute(f"drop view if exists {view}")
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_missing_table_returns_none_without_failing(dbt, sf, run_id):
    name = f"UC2_MISSING_{run_id}"
    got = run_macro("it_metadata", {"relations": [rel(name)], "since": utc_now(sf)})[name]
    assert got == {"row_count": None, "rows_added": None, "last_change": None}
