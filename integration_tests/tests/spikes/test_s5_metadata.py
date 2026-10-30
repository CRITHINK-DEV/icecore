"""Spike S5: are INFORMATION_SCHEMA.TABLES.ROW_COUNT and SYSTEM$LAST_CHANGE_COMMIT_TIME reliable per table kind?

Each probe creates its table in SPIKES with the run_id in the name, writes 100 rows through the
s5_write macro, reads metadata through s5_metadata right after the write and again 60 s later,
records what it saw in spikes/results/s5.json and drops the table.
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest
from dbt.cli.main import dbtRunner

from conftest import IT_DIR
from helpers.findings import record_finding

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
N_ROWS = 100
WAIT_S = 60
RESULT_PREFIX = "icecore_it_result:"
KINDS = ["standard", "transient", "dynamic", "iceberg"]


def quoted(*parts: str) -> str:
    """Fully quoted three-part name; embedded double quotes are doubled (adapter.quote does not do it)."""
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def run_macro(macro: str, args: dict):
    """Run a macro through dbt run-operation and return the JSON value it logged with RESULT_PREFIX."""
    seen = []

    def capture(event):
        msg = getattr(event.data, "msg", "")
        if event.info.name == "JinjaLogInfo" and msg.startswith(RESULT_PREFIX):
            seen.append(json.loads(msg[len(RESULT_PREFIX):]))

    res = dbtRunner(callbacks=[capture]).invoke(
        ["run-operation", macro, "--args", json.dumps(args), "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]
    )
    assert res.success, f"{macro} failed: {res.exception}"
    return seen[-1] if seen else None


def utc_now(sf) -> str:
    return sf.cursor().execute(
        """select to_varchar(sysdate(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"')"""
    ).fetchone()[0]


def iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def create_kind(sf, kind: str, name: str, run_id: str) -> tuple[str, str, list[str]]:
    """Create the table of `kind`; return (relation to read, relation to write, drop statements)."""
    target = quoted(DATABASE, SCHEMA, name)
    cur = sf.cursor()
    if kind == "standard":
        cur.execute(f"create table {target} (id number, note varchar)")
        return target, target, [f"drop table if exists {target}"]
    if kind == "transient":
        cur.execute(f"create transient table {target} (id number, note varchar)")
        return target, target, [f"drop table if exists {target}"]
    if kind == "dynamic":
        source = quoted(DATABASE, SCHEMA, f"{name}_SRC")
        cur.execute(f"create table {source} (id number, note varchar)")
        cur.execute(
            f"create dynamic table {target} target_lag = downstream warehouse = {os.environ['SNOWFLAKE_WAREHOUSE']} "
            f"initialize = on_create as select id, note from {source}"
        )
        return target, source, [f"drop dynamic table if exists {target}", f"drop table if exists {source}"]
    if kind == "iceberg":
        volume = os.environ["ICECORE_EXTERNAL_VOLUME"]
        cur.execute(
            f"create iceberg table {target} (id int, note string) catalog = 'SNOWFLAKE' "
            f"external_volume = '{volume}' base_location = 'icecore_s5_{run_id}'"
        )
        return target, target, [f"drop iceberg table if exists {target}"]
    raise ValueError(kind)


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", KINDS)
def test_s5_row_count_and_change_time(dbt, sf, run_id, kind):
    if kind == "iceberg" and not os.environ.get("ICECORE_EXTERNAL_VOLUME"):
        reason = "ICECORE_EXTERNAL_VOLUME is not set: the sandbox has no external volume for Iceberg tables"
        record_finding("s5", kind, {"skipped": reason})
        pytest.skip(reason)

    name = f"S5_{kind.upper()}_{run_id}"
    target, write_target, drops = create_kind(sf, kind, name, run_id)
    try:
        empty = run_macro("s5_metadata", {"relation": target})
        write_at = utc_now(sf)
        run_macro("s5_write", {"relation": write_target, "n": N_ROWS})
        if kind == "dynamic":
            sf.cursor().execute(f"alter dynamic table {target} refresh")
        written = time.monotonic()
        m0 = run_macro("s5_metadata", {"relation": target})
        m0["read_after_s"] = round(time.monotonic() - written, 1)
        time.sleep(max(0.0, WAIT_S - (time.monotonic() - written)))
        m60 = run_macro("s5_metadata", {"relation": target})
        m60["read_after_s"] = round(time.monotonic() - written, 1)

        def verdict(m: dict) -> dict:
            return {
                "row_count_ok": m["row_count"] == N_ROWS,
                "last_change_at_or_after_write": m["last_change"] is not None and iso(m["last_change"]) >= iso(write_at),
            }

        record_finding("s5", kind, {
            "write_path": "s5_write on the source table, then ALTER DYNAMIC TABLE ... REFRESH" if kind == "dynamic" else "s5_write",
            "write_started_at": write_at,
            "empty": empty,
            "t0": m0 | verdict(m0),
            "t60": m60 | verdict(m60),
        })
        assert m60["row_count"] == N_ROWS and m60["last_change"] is not None
    finally:
        for stmt in drops:
            sf.cursor().execute(stmt)


@pytest.mark.sandbox
def test_s5_quoted_identifier_table(dbt, sf, run_id):
    name = f'Mixed Case "Orders" {run_id}'
    target = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create table {target} (id number, note varchar)")
    try:
        write_at = utc_now(sf)
        run_macro("s5_write", {"relation": target, "n": N_ROWS})
        written = time.monotonic()
        m = run_macro("s5_metadata", {"relation": target})
        while m["row_count"] != N_ROWS and time.monotonic() - written < WAIT_S:
            time.sleep(5)
            m = run_macro("s5_metadata", {"relation": target})
        m["read_after_s"] = round(time.monotonic() - written, 1)
        adapter_quoted = run_macro("s5_adapter_quote", {"identifier": name})
        record_finding("s5", "quoted_identifier", {
            "relation": target,
            "write_started_at": write_at,
            "metadata": m,
            "adapter_quote_output": adapter_quoted,
            "adapter_quote_escapes_embedded_quotes": adapter_quoted == '"' + name.replace('"', '""') + '"',
        })
        assert m["row_count"] == N_ROWS
        assert m["last_change"] is not None and iso(m["last_change"]) >= iso(write_at)
    finally:
        sf.cursor().execute(f"drop table if exists {target}")
