"""UC2 multi-column reader: icecore.compute_columns_metrics reads every column of a model in one query."""
import time

import pytest

from helpers.macros import run_macro, run_macro_with_logs
from test_columns import DATABASE, SCHEMA, SINCE, quoted, rel, scan_stats, utc_now

NUM = [f"N{i}" for i in range(10)]
TXT = [f"T{i}" for i in range(6)]
TS = [f"D{i}" for i in range(4)]
COLS = NUM + TXT + TS


def render(columns, window, metrics=None):
    args = {"rel": rel("fct_orders"), "columns": columns, "window": window}
    if metrics is not None:
        args["metrics"] = metrics
    return run_macro("it_columns_metrics_sql", args)


# --- offline ----------------------------------------------------------------------------------------

def test_one_select_list_over_one_window_source():
    sql = " ".join(render(["a", "b", "c"], {"mode": "changes", "since": SINCE}, ["null_rate", "avg"]).split())
    assert sql.lower().count("changes (information") == 1
    assert 'select "A", "B", "C" from' in sql
    assert sql.count("'avg', avg(") == 3 and "'0'," in sql and "'2'," in sql
    assert "select *" not in sql.lower()


def test_timestamp_window_is_one_query_too():
    w = {"mode": "timestamp_column", "since": SINCE, "timestamp_column": "u", "timestamp_type": "TIMESTAMP_NTZ"}
    sql = render(["a", "b"], w)
    assert sql.count('"U" >= to_timestamp_ntz') == 1


def test_column_names_never_become_string_literals():
    sql = render(['Net "Amount"', "x' or 1=1 --"], {"mode": "changes", "since": SINCE})
    assert "'x'" not in sql and "or 1=1" in sql  # only inside a quoted identifier
    assert '"x\' or 1=1 --"' in sql
    assert '"Net ""Amount"""' in sql or 'Net ""Amount""' in sql


# --- sandbox ----------------------------------------------------------------------------------------

def make_wide_table(sf, table, change_tracking=True):
    cols = ", ".join([f"{c} number" for c in NUM] + [f"{c} varchar" for c in TXT] + [f"{c} timestamp_ntz" for c in TS])
    sf.cursor().execute(f"create transient table {table} ({cols})" + (" change_tracking = true" if change_tracking else ""))


def fill(sf, table, n, start=0):
    nums = ", ".join(f"iff(seq4() % {i + 2} = 0, null, seq4() * {i + 1} + {start})" for i in range(len(NUM)))
    txts = ", ".join(f"iff(seq4() % {i + 3} = 0, null, 'v' || seq4())" for i in range(len(TXT)))
    tss = ", ".join(f"iff(seq4() % {i + 4} = 0, null, convert_timezone('UTC', current_timestamp())::timestamp_ntz)" for i in range(len(TS)))
    sf.cursor().execute(f"insert into {table} select {nums}, {txts}, {tss} from table(generator(rowcount => {n}))")


@pytest.mark.sandbox
def test_twenty_columns_match_the_per_column_path_and_scan_once(dbt, sf, run_id):
    name = f"UC2_MULTI_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    make_wide_table(sf, table)
    try:
        fill(sf, table, 2000)
        time.sleep(2)
        since = utc_now(sf)
        fill(sf, table, 300, start=10_000)
        sf.cursor().execute(f"update {table} set N0 = null where N1 % 5 = 0")
        metrics = ["window_rows", "null_rate", "avg", "min", "max"]
        got = run_macro("it_columns_equivalence", {"rel": rel(name.lower()), "columns": COLS, "since": since, "metrics": metrics})
        assert got["window"]["mode"] == "changes", got
        assert len(got["multi"]) == 20
        assert got["multi"] == got["single"], got
        assert got["multi"]["N0"]["window_rows"] > 300 and got["multi"]["T0"].keys() == {"window_rows", "null_rate"}

        sql = run_macro("it_columns_metrics_sql", {"rel": rel(name.lower()), "columns": COLS, "window": got["window"]})  # default metrics suit every type
        one = run_macro("it_columns_metrics_sql", {"rel": rel(name.lower()), "columns": ["n0"], "window": got["window"]})

        def table_scans(query):
            cur = sf.cursor()
            cur.execute("alter session set use_cached_result = false")
            cur.execute(query).fetchone()
            qid = cur.sfqid
            return cur.execute(
                "select count(*), sum(operator_statistics:pruning:partitions_scanned::number) "
                f"from table(get_query_operator_stats('{qid}')) where operator_type = 'TableScan'").fetchone()

        multi_ops, multi_parts = table_scans(sql)
        one_ops, one_parts = table_scans(one)
        print("TableScan operators and partitions scanned: 20 columns", (multi_ops, multi_parts), "one column", (one_ops, one_parts))
        # a CHANGES read is one scan operator per query whatever the column count (S5: it reads old and new
        # versions after DML); 20 columns in one query read the same partitions as one column, not 20 times that
        assert multi_ops == one_ops and multi_parts == one_parts, ((multi_ops, multi_parts), (one_ops, one_parts))
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_multi_column_falls_back_once_and_reports_missing_columns(dbt, sf, run_id):
    name = f"UC2_MULTI_FB_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    make_wide_table(sf, table, change_tracking=False)
    try:
        fill(sf, table, 50)
        time.sleep(2)
        since = "2000-01-01T00:00:00.000000Z"
        got, logs = run_macro_with_logs("it_columns_equivalence", {
            "rel": rel(name.lower()), "columns": ["n0", "t0", "nope"][:2] + ["d1"], "since": since, "timestamp_column": "d1"})
        assert got["window"]["mode"] == "timestamp_column", got
        assert got["multi"] == got["single"], got
        got2 = run_macro("it_columns_equivalence", {
            "rel": rel(name.lower()), "columns": ["n0", "nope"], "since": since, "timestamp_column": "d1"})
        assert got2["multi"]["nope"] is None and got2["multi"]["n0"] == got2["single"]["n0"], got2
    finally:
        sf.cursor().execute(f"drop table if exists {table}")
