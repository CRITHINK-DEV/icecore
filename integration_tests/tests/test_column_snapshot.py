"""UC8 schema_columns_snapshot holds every column on every run (macros/views/schema_columns.sql, compat view
macros/compat/elementary_metrics.sql). The schema_changes test writes the full column state each run as chunked
`icecore.schema_signature` records; the SCHEMA_COLUMNS core view and the compat view read it back.

Sandbox: tables named with a run_id in ICECORE_SANDBOX.SPIKES, an obs schema of its own, dropped afterwards.
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import install_deps, invoke, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = "SPIKES"
EVENTS = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
          " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'"
          " and record:name::string = 'icecore.schema_signature'")
N_WIDE = 300


def quoted(table: str) -> str:
    return f'"{DATABASE}"."{SCHEMA}"."{table}"'


def wide_name(i: int) -> str:
    return f"WIDE_COLUMN_NUMBER_{i:04d}_" + "X" * 40


# --- offline ----------------------------------------------------------------------------------------

def test_fixtures_parse_and_register_the_snapshot_tests():
    install_deps()
    res = invoke(["parse"])
    assert res.success
    names = {n.name for n in res.result.nodes.values() if n.resource_type.value == "test"}
    assert {"it_snapshot_snap", "it_snapshot_wide"} <= names


# --- sandbox ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def snap(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    small, wide = f"IT_SNAP_{run_id}".upper(), f"IT_SNAPW_{run_id}_{'L' * 150}".upper()
    base = {"it_uc8_snap_table": small, "it_uc8_wide_table": wide}
    # payload_cap_kb 1 forces the wide table across many records; its long name takes room from every record
    obs = {"icecore": {"obs_schema": schema, "payload_cap_kb": 1}, **base}
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        cur.execute(f'create table {quoted(small)} (id number, note varchar(20), gone date)')
        cols = ", ".join(f'"{wide_name(i)}" number' for i in range(N_WIDE))
        cur.execute(f'create table {quoted(wide)} ({cols})')
        since = cur.execute("select dateadd(minute, -2, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]
        select = ["--select", "it_snapshot_snap", "it_snapshot_wide"]
        runs = []
        for step in range(3):
            if step == 2:  # the third run follows a dropped column
                cur.execute(f'alter table {quoted(small)} drop column gone')
            runs.append(invoke(["test", *select, "--vars", json.dumps(obs)]))
            assert runs[-1].exception is None
            time.sleep(15)  # the runs' snapshots differ by event time; the view orders snapshots by it
        wait_for_events(sf, EVENTS, {"db": DATABASE, "schema": schema}, min_rows=9, since=since, settle_s=15)
        view = f'"{DATABASE}"."{schema}"."SCHEMA_COLUMNS"'
        cur.execute(f"create view {view} as " + run_macro("it_schema_columns_view_real", {"schema": schema},
                                                         ["--vars", json.dumps(obs)]))
        rows = sf.cursor(DictCursor).execute(f"select * from {view}").fetchall()
        yield {"small": small, "wide": wide, "rows": [{k.lower(): v for k, v in r.items()} for r in rows],
               "view": view, "schema": schema, "since": since,
               "statuses": [{r.node.name: r.status.value for r in run.result.results} for run in runs]}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        for table in (small, wide):
            cur.execute(f"drop table if exists {quoted(table)}")


def by_run(d: dict, table: str) -> list[dict]:
    """The column rows of one table, one set per run (invocation), oldest run first."""
    runs: dict = {}
    for r in d["rows"]:
        if r["relation"] == quoted(table):
            runs.setdefault((r["snapshot_at"], r["invocation_id"]), []).append(r)
    return [runs[k] for k in sorted(runs)]


@pytest.mark.sandbox
def test_an_unchanged_table_holds_all_its_columns_on_both_runs(snap):
    runs = by_run(snap, snap["small"])
    assert len(runs) == 3
    first, second = ({r["column"] for r in run} for run in runs[:2])
    assert first == second == {"ID", "NOTE", "GONE"}
    assert [r["is_new"] for r in runs[0]] == [False] * 3 and [r["is_new"] for r in runs[1]] == [False] * 3


@pytest.mark.sandbox
def test_a_removed_column_is_absent_from_the_latest_snapshot(snap):
    runs = by_run(snap, snap["small"])
    assert {r["column"] for r in runs[-1]} == {"ID", "NOTE"}
    assert "GONE" in {r["column"] for r in runs[-2]}  # it was there before


@pytest.mark.sandbox
def test_a_wide_table_under_a_tiny_payload_cap_loses_no_column(snap, sf):
    runs = by_run(snap, snap["wide"])
    assert len(runs) == 3
    for run in runs:
        assert sorted(r["column"] for r in run) == sorted(wide_name(i) for i in range(N_WIDE))
    # it really was split: more than one record per run, none cut
    records = wait_for_events(sf, EVENTS + " and record_attributes:\"dbt.node.relation\"::string = %(rel)s",
                              {"db": DATABASE, "schema": snap["schema"], "rel": quoted(snap["wide"])},
                              min_rows=3 * 2, since=snap["since"])
    assert all("icecore.truncated" not in r["record_attributes"] for r in records)
    counts = {r["record_attributes"]["dbt.schema.chunk_count"] for r in records}
    assert min(counts) > 1 and len(records) == 3 * min(counts)


@pytest.mark.sandbox
def test_the_compat_view_has_the_latest_snapshot_of_every_column(snap, sf):
    compat = f"{snap['schema']}_ELEMENTARY"
    created = sf.cursor()
    created.execute(f'create schema "{DATABASE}"."{compat}"')
    try:
        sql = run_macro("it_compat_snapshot_sql", {"obs": f'"{DATABASE}"."{snap["schema"]}"'})
        created.execute(f'create view "{DATABASE}"."{compat}"."SCHEMA_COLUMNS_SNAPSHOT" as {sql}')
        rows = sf.cursor(DictCursor).execute(
            f'select * from "{DATABASE}"."{compat}"."SCHEMA_COLUMNS_SNAPSHOT" where full_table_name = %s '
            "qualify detected_at = max(detected_at) over (partition by full_table_name)",
            (f"{DATABASE}.{SCHEMA}.{snap['small']}",)).fetchall()
        assert sorted(r["COLUMN_NAME"] for r in rows) == ["ID", "NOTE"]
    finally:
        created.execute(f'drop schema if exists "{DATABASE}"."{compat}" cascade')


@pytest.mark.sandbox
def test_a_chunked_baseline_is_read_back_whole(snap):
    # runs 2 and 3 compare the wide table with its multi-record snapshot: no phantom removed or added column
    assert [st["it_snapshot_wide"] for st in snap["statuses"]] == ["pass", "pass", "pass"]
    assert [st["it_snapshot_snap"] for st in snap["statuses"]] == ["pass", "pass", "warn"]  # only the dropped column


@pytest.mark.sandbox
def test_an_incomplete_snapshot_is_neither_listed_nor_used_as_baseline(sf):
    """Invocation 1 lands both chunks of a snapshot, invocation 2 only chunk 0 of two: the view lists invocation 1 only,
    and the baseline read is invocation 1's three columns, not the newest partial set."""
    from helpers.run_hooks import obs_schema
    run_id = uuid.uuid4().hex[:8].upper()
    table = f"IT_PARTIAL_{run_id}"
    rel = f'"{DATABASE}"."{SCHEMA}"."{table}"'

    def chunk(cols, idx, count):
        return {"kind": "schema_signature", "attributes": {
            "dbt.node.relation": rel, "dbt.schema.columns": json.dumps([{"name": c, "data_type": "TEXT"} for c in cols]),
            "dbt.schema.chunk_index": idx, "dbt.schema.chunk_count": count}}

    with obs_schema(sf) as obs:
        obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": obs}})]
        since = sf.cursor().execute("select dateadd(minute, -2, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]
        run_macro("it_compat_emit_events", {"events": [chunk(["A", "B"], 0, 2), chunk(["C"], 1, 2)]}, obs_vars)
        time.sleep(3)
        run_macro("it_compat_emit_events", {"events": [chunk(["A"], 0, 2)]}, obs_vars)
        wait_for_events(sf, EVENTS + " and record_attributes:\"dbt.node.relation\"::string = %(rel)s",
                        {"db": DATABASE, "schema": obs, "rel": rel}, min_rows=3, since=since, settle_s=15)
        view = f'"{DATABASE}"."{obs}"."SCHEMA_COLUMNS"'
        sf.cursor().execute(f"create view {view} as " + run_macro("it_schema_columns_view_real", {"schema": obs}, obs_vars))
        rows = sf.cursor(DictCursor).execute(f"select * from {view} where relation = %s", (rel,)).fetchall()
        assert sorted(r["COLUMN"] for r in rows) == ["A", "B", "C"] and len({r["INVOCATION_ID"] for r in rows}) == 1
        baseline = run_macro("it_previous_signature", {"rel": {"database": DATABASE, "schema": SCHEMA, "identifier": table,
                                                              "quoted": True}}, obs_vars)
        assert sorted(c["name"] for c in baseline) == ["A", "B", "C"]
