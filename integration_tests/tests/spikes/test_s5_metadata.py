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


# --- round 2: dbt write patterns and the CHANGES fallback -----------------------------------------

@pytest.mark.sandbox
def test_s5_dbt_write_patterns(dbt, sf, run_id):
    """The statements dbt materializations issue: metadata must follow each one."""
    name = f"S5_PATTERNS_{run_id}".upper()
    target = quoted(DATABASE, SCHEMA, name)
    gen = "select seq4() as id, 's5'::varchar as note from table(generator(rowcount => {n}))"
    steps = [
        ("ctas", f"create transient table {target} as " + gen.format(n=100), 100),
        ("create_or_replace_as_select", f"create or replace transient table {target} as " + gen.format(n=250), 250),
        ("delete", f"delete from {target} where id < 50", 200),
        ("insert_overwrite", f"insert overwrite into {target} " + gen.format(n=7), 7),
        # dbt's default incremental strategy on Snowflake: 7 ids matched and updated, 3 inserted
        ("merge", f"merge into {target} t using ({gen.format(n=10)}) s on t.id = s.id "
                  "when matched then update set note = 'merged' "
                  "when not matched then insert (id, note) values (s.id, s.note)", 10),
        ("truncate", f"truncate table {target}", 0),
    ]
    seen, previous_token = {}, 0
    try:
        for label, sql, expected in steps:
            started = utc_now(sf)
            sf.cursor().execute(sql)
            m = run_macro("s5_metadata", {"relation": target})
            token = int(m["last_change_token"])
            seen[label] = {
                "expected_rows": expected,
                "row_count": m["row_count"],
                "row_count_ok": m["row_count"] == expected,
                "last_change": m["last_change"],
                "last_change_token": m["last_change_token"],
                "token_increased": token > previous_token,
                "last_change_at_or_after_statement": m["last_change"] is not None and iso(m["last_change"]) >= iso(started),
            }
            previous_token = token
        record_finding("s5", "dbt_write_patterns", seen)
        assert all(s["row_count_ok"] and s["token_increased"] for s in seen.values()), seen
    finally:
        sf.cursor().execute(f"drop table if exists {target}")


def _query_stats(sf, query_ids: dict) -> dict:
    """bytes_scanned from QUERY_HISTORY_BY_SESSION, partitions from GET_QUERY_OPERATOR_STATS (TableScan operators)."""
    ids = ", ".join(f"'{q}'" for q in query_ids.values())
    rows = sf.cursor().execute(
        "select query_id, bytes_scanned "
        f"from table(information_schema.query_history_by_session(result_limit => 200)) where query_id in ({ids})"
    ).fetchall()
    bytes_by_id = dict(rows)
    out = {}
    for label, q in query_ids.items():
        scanned, total = sf.cursor().execute(
            "select sum(operator_statistics:pruning:partitions_scanned::number), sum(operator_statistics:pruning:partitions_total::number) "
            f"from table(get_query_operator_stats('{q}')) where operator_type = 'TableScan'"
        ).fetchone()
        out[label] = {"bytes_scanned": bytes_by_id.get(q), "partitions_scanned": scanned, "partitions_total": total}
    return out


@pytest.mark.sandbox
def test_s5_changes_fallback(dbt, sf, run_id):
    """COUNT over CHANGES against a full count, per change window: inserts only, a DELETE and a MERGE
    spread across the table; and whether it works on views."""
    base = quoted(DATABASE, SCHEMA, f"S5_CHG_{run_id}".upper())
    plain = quoted(DATABASE, SCHEMA, f"S5_CHG_PLAIN_{run_id}".upper())
    view_tracked = quoted(DATABASE, SCHEMA, f"S5_CHG_V_{run_id}".upper())
    view_untracked = quoted(DATABASE, SCHEMA, f"S5_CHG_VPLAIN_{run_id}".upper())
    net = "count_if(metadata$action = 'INSERT') - count_if(metadata$action = 'DELETE')"  # the UC2 reader's query
    full_scan = f"select count(*) from {base} where note like '%-%'"
    cur = sf.cursor()
    finding = {}

    def window(statement: str, queries: list[tuple[str, str]]) -> tuple[dict, dict]:
        time.sleep(2)
        since = utc_now(sf)
        cur.execute(statement)
        at = f"changes (information => default) at (timestamp => to_timestamp_tz('{since}'))"
        ids, counts = {}, {}
        for label, sql in queries:
            counts[label] = cur.execute(sql.format(at=at)).fetchone()[0]
            ids[label] = cur.sfqid
        time.sleep(5)  # query history by session is not instant
        stats = _query_stats(sf, ids)
        return {label: {"count": counts[label], **(stats[label] or {})} for label in ids}, counts

    try:
        cur.execute("alter session set use_cached_result = false")
        cur.execute(f"create transient table {base} (id number, note varchar) change_tracking = true")
        for batch in range(10):  # ten inserts: at least ten micro-partitions
            cur.execute(
                f"insert into {base} select seq4() + {batch * 100000}, uuid_string() from table(generator(rowcount => 100000))"
            )
        cur.execute(f"create transient table {plain} (id number, note varchar)")
        cur.execute(f"create view {view_tracked} change_tracking = true as select id, note from {base}")
        cur.execute(f"create view {view_untracked} as select id, note from {plain}")

        # window 1: inserts only
        stats, counts = window(
            f"insert into {base} select seq4() + 1000000, uuid_string() from table(generator(rowcount => {N_ROWS}))",
            [
                ("full_count_metadata", f"select count(*) from {base}"),
                ("full_count_scan", full_scan),
                ("changes_count", f"select count(*) from {base} {{at}}"),
                ("changes_net", f"select {net} from {base} {{at}}"),
                ("changes_count_view_tracked", f"select count(*) from {view_tracked} {{at}}"),
            ],
        )
        finding["inserts_only"] = stats
        assert counts["changes_net"] == N_ROWS and counts["changes_count_view_tracked"] == N_ROWS

        # window 2: DELETE of 10 rows, one in each of the ten 100k-row batches
        stats, counts = window(
            f"delete from {base} where mod(id, 100000) = 7 and id < 1000000",
            [("changes_net", f"select {net} from {base} {{at}}"), ("full_count_scan", full_scan)],
        )
        finding["delete_10_rows_across_partitions"] = stats
        assert counts["changes_net"] == -10

        # window 3: MERGE (dbt's default incremental strategy): 10 rows updated across the batches, 50 inserted
        stats, counts = window(
            f"merge into {base} t using ("
            f"  select id, 'merged' as note from {base} where mod(id, 100000) = 13 and id < 1000000"
            f"  union all select seq4() + 2000000, uuid_string() from table(generator(rowcount => 50))"
            f") s on t.id = s.id when matched then update set note = s.note "
            f"when not matched then insert (id, note) values (s.id, s.note)",
            [("changes_net", f"select {net} from {base} {{at}}"), ("full_count_scan", full_scan)],
        )
        finding["merge_10_updates_across_partitions_50_inserts"] = stats
        assert counts["changes_net"] == 50

        since = utc_now(sf)
        cur.execute(f"insert into {plain} select seq4(), 's5' from table(generator(rowcount => {N_ROWS}))")
        try:
            cur.execute(f"select count(*) from {view_untracked} changes (information => default) "
                        f"at (timestamp => to_timestamp_tz('{since}'))")
            finding["view_without_change_tracking"] = {"ok": True}
        except Exception as e:  # noqa: BLE001 - the error is the finding
            finding["view_without_change_tracking"] = {"ok": False, "error": str(e).splitlines()[-1][:200]}
        record_finding("s5", "changes_fallback", finding)
    finally:
        cur.execute(f"drop view if exists {view_tracked}")
        cur.execute(f"drop view if exists {view_untracked}")
        cur.execute(f"drop table if exists {base}")
        cur.execute(f"drop table if exists {plain}")
