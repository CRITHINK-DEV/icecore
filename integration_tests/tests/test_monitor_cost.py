"""Cost checks for the UC2 monitors: the zero-scan ones stay at zero bytes and the incremental ones read only the
change window. Measured in QUERY_HISTORY (information_schema.query_history_by_user, a Snowflake-clock window), per
monitor, on a 100000-row table loaded in 20 batches so it has many micro-partitions.

- Zero-scan (freshness, volume, schema_changes): every query that names the table scans 0 bytes, except the read of
  INFORMATION_SCHEMA.TABLES behind icecore.get_row_count_metadata, which scans a metadata view of a few hundred
  bytes, never the table.
- Incremental (column_anomaly after 50 inserted rows): the bytes scanned by its queries are far below a full scan of
  the same column. 
Partitions come from GET_QUERY_OPERATOR_STATS(query_id) per table scan (the information_schema query history has
no partitions column). The reads of the event table (the schema_changes baseline, the column_anomaly previous
observation) are measured on their own, apart from the monitored table, and bounded.
The result cache is switched off on the harness session only (the fixture's own full-scan baseline); the dbt sessions
run with it on, which cannot help here: every query names a run_id-named table, so none has run before.
The numbers are printed (pytest -s, or -rP) and attached to the PR.
"""
import json
import os
import re
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.macros import invoke, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = "SPIKES"
BATCHES, BATCH_ROWS, CHANGED_ROWS = 20, 5000, 50
METADATA_VIEW_BYTES = 1_000_000  # the INFORMATION_SCHEMA.TABLES read: a metadata view, not the table
INCREMENTAL_MAX_SHARE = 0.25     # change window bytes over full scan bytes
EVENT_READ_MAX_BYTES = 5_000_000  # a tripwire for the bounded history read: measured 20 KB (baseline) and 218 KB (previous observation)


def quoted(table: str) -> str:
    return f'"{DATABASE}"."{SCHEMA}"."{table}"'


def history(sf, since) -> list:
    return sf.cursor(DictCursor).execute(
        "select query_id, query_text, query_type, bytes_scanned, rows_produced from table(information_schema."
        "query_history_by_user(user_name => current_user(), end_time_range_start => %(t)s::timestamp_ltz, "
        "result_limit => 10000))", {"t": since}).fetchall()


def _relevant(rows: list, table: str) -> list:
    """The queries that name the table, minus the harness's own and the telemetry writes (CALLs of ICECORE_EMIT)."""
    out = []
    for r in rows:
        text = r["QUERY_TEXT"].lower()
        if table.lower() not in text or r["QUERY_TYPE"] in ("INSERT", "CREATE_TABLE_AS_SELECT", "CREATE_TABLE", "DROP"):
            continue
        if re.search(r'call\s+"[^"]+"\."[^"]+"\."icecore_emit', text) or "query_history" in text or "explain" in text or "get_query_operator_stats" in text:
            continue
        out.append(r)
    return out


def touching(rows: list, table: str) -> list:
    """Queries on the monitored table (metadata reads, SHOW, CHANGES)."""
    return [r for r in _relevant(rows, table) if "resource_attributes" not in r["QUERY_TEXT"].lower()]


def event_reads(rows: list, table: str) -> list:
    """Queries on the event table that look for the monitored table's records (the history reads)."""
    return [r for r in _relevant(rows, table) if "resource_attributes" in r["QUERY_TEXT"].lower()]


def scans(sf, query_id: str) -> list[dict]:
    """Per table scan of a query: {table, scanned, total}, from GET_QUERY_OPERATOR_STATS."""
    rows = sf.cursor(DictCursor).execute(f"select operator_type, operator_attributes, operator_statistics "
                                         f"from table(get_query_operator_stats('{query_id}'))").fetchall()
    out = []
    for r in rows:
        if r["OPERATOR_TYPE"] != "TableScan":
            continue
        attrs, stats = json.loads(r["OPERATOR_ATTRIBUTES"]), json.loads(r["OPERATOR_STATISTICS"])
        pruning = stats.get("pruning", {})
        out.append({"table": attrs.get("table_name"), "scanned": pruning.get("partitions_scanned", 0),
                    "total": pruning.get("partitions_total", 0)})
    return out


def partitions(sf, sql: str) -> int:
    plan = json.loads(sf.cursor().execute("explain using json " + sql).fetchone()[0])
    return int(plan["GlobalStats"]["partitionsTotal"])


@pytest.fixture(scope="module")
def cost(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    table = f"IT_COST_{run_id}".upper()
    obs = {"icecore": {"obs_schema": schema}}
    cur = sf.cursor()
    cur.execute("alter session set use_cached_result = false")
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps(obs)])
        cur.execute(f'create table {quoted(table)} (id number, amount number(10, 2), payload varchar) change_tracking = true')
        time.sleep(3)
        since = cur.execute("select sysdate()").fetchone()[0].strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        time.sleep(2)
        for b in range(BATCHES):
            cur.execute(f"insert into {quoted(table)} select {b} * {BATCH_ROWS} + seq4(), uniform(0, 1000, random()), "
                        f"randstr(200, random()) from table(generator(rowcount => {BATCH_ROWS}))")
        total = cur.execute(f"select count(*) from {quoted(table)}").fetchone()[0]
        n_partitions = partitions(sf, f"select amount from {quoted(table)}")
        # the baseline: a full scan of the one column the incremental monitor reads
        full_t0 = cur.execute("select dateadd(second, -2, current_timestamp())").fetchone()[0]
        cur.execute(f"select count_if(amount is null) / count(*), avg(amount), max(amount) from {quoted(table)}")
        full_id = cur.sfqid
        full_bytes = next(r["BYTES_SCANNED"] for r in history(sf, full_t0) if r["QUERY_ID"] == full_id)
        # 50 more rows, after the window start
        window_since = cur.execute("select sysdate()").fetchone()[0].strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        time.sleep(2)
        cur.execute(f"insert into {quoted(table)} select 10000000 + seq4(), uniform(0, 1000, random()), "
                    f"randstr(200, random()) from table(generator(rowcount => {CHANGED_ROWS}))")
        variables = {**obs, "it_uc2_cost_table": table, "it_since": window_since}
        measured = {}
        for name in ("it_cost_freshness", "it_cost_volume", "it_cost_schema_changes", "it_cost_column_anomaly",
                     "it_cost_column_history"):
            t0 = cur.execute("select dateadd(second, -2, current_timestamp())").fetchone()[0]
            res = invoke(["test", "--select", name, "--vars", json.dumps(variables)])
            assert res.success and res.result.results[0].status.value == "pass", name
            time.sleep(3)  # query history is near real time
            got = history(sf, t0)
            measured[name] = {"table": touching(got, table), "events": event_reads(got, table)}
            for kind in ("table", "events"):
                for r in measured[name][kind]:
                    r["SCANS"] = scans(sf, r["QUERY_ID"])
        def line(r):
            return {"bytes_scanned": r["BYTES_SCANNED"], "type": r["QUERY_TYPE"], "scans": r["SCANS"],
                    "text": " ".join(r["QUERY_TEXT"].split())[:110]}

        report = {name: {kind: [line(r) for r in rows] for kind, rows in parts.items()} for name, parts in measured.items()}
        report["_table"] = {"rows": total, "micro_partitions": n_partitions, "full_scan_bytes_of_amount": full_bytes,
                            "changed_rows": CHANGED_ROWS}
        print("\nICECORE_MONITOR_COST " + json.dumps(report, indent=1))
        yield {"table": table, "measured": measured, "full_bytes": full_bytes, "partitions": n_partitions, "rows": total}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        cur.execute(f"drop table if exists {quoted(table)}")


@pytest.mark.sandbox
def test_the_table_is_big_enough_to_tell_a_scan_from_a_read_of_metadata(cost):
    assert cost["rows"] == BATCHES * BATCH_ROWS
    assert cost["partitions"] >= 10
    assert cost["full_bytes"] > 100_000


def _mine(scan_list: list, table: str) -> list:
    return [x for x in scan_list if x["table"] and table.lower() in x["table"].lower()]


@pytest.mark.sandbox
@pytest.mark.parametrize("name", ["it_cost_freshness", "it_cost_volume", "it_cost_schema_changes"])
def test_zero_scan_monitors_scan_zero_bytes_and_zero_partitions_of_the_table(cost, name):
    rows = cost["measured"][name]["table"]
    assert rows, f"no query of {name} found in the query history"
    for r in rows:
        text = r["QUERY_TEXT"].lower()
        assert not re.search(r"\bfrom\s+\"?" + DATABASE.lower() + r"\"?\.\"?" + SCHEMA.lower() + r"\"?\.\"?it_cost_", text), text
        assert _mine(r["SCANS"], cost["table"]) == [], (text, r["SCANS"])  # 0 partitions of the monitored table
        if "information_schema" in text:
            assert (r["BYTES_SCANNED"] or 0) < METADATA_VIEW_BYTES, (text, r["BYTES_SCANNED"])
        else:
            assert not r["BYTES_SCANNED"], (text, r["BYTES_SCANNED"])


@pytest.mark.sandbox
def test_volume_reads_metadata_only(cost):
    texts = [r["QUERY_TEXT"].lower() for r in cost["measured"]["it_cost_volume"]["table"]]
    assert any("information_schema.tables" in t for t in texts)


@pytest.mark.sandbox
def test_column_anomaly_scans_only_the_change_window(cost):
    rows = cost["measured"]["it_cost_column_anomaly"]["table"]
    changes = [r for r in rows if " changes " in " ".join(r["QUERY_TEXT"].lower().split())]
    assert changes, "the CHANGES query was not found in the query history"
    scanned = sum(r["BYTES_SCANNED"] or 0 for r in rows if "information_schema" not in r["QUERY_TEXT"].lower())
    print(f"\nICECORE_MONITOR_COST column_anomaly scanned {scanned} bytes; full scan of the column {cost['full_bytes']}")
    assert 0 < scanned < INCREMENTAL_MAX_SHARE * cost["full_bytes"], (scanned, cost["full_bytes"])
    own = [x for r in changes for x in _mine(r["SCANS"], cost["table"])]
    assert own, "no table scan of the monitored table in the CHANGES query's operator stats"
    for x in own:
        # the CHANGES scan's own total counts only the partitions of the change window, so compare with the table's
        assert 0 < x["scanned"] < cost["partitions"], (x, cost["partitions"])


@pytest.mark.sandbox
@pytest.mark.parametrize("name", ["it_cost_schema_changes", "it_cost_column_history"])
def test_the_bounded_history_reads_of_the_event_table_stay_small(cost, name):
    reads = cost["measured"][name]["events"]
    assert reads, f"{name} did not read the event table (the baseline / previous observation read)"
    total = sum(r["BYTES_SCANNED"] or 0 for r in reads)
    print(f"\nICECORE_MONITOR_COST {name} event-table reads: {total} bytes in {len(reads)} queries")
    assert total < EVENT_READ_MAX_BYTES, (name, total)
