"""UC2 incremental column metrics: icecore.get_change_window and icecore.compute_column_metrics.

Called through the wrappers in integration_tests/macros/test_support/it_columns.sql. Scan figures come from
running the SQL the macro renders in the test's own session, with the result cache off, and reading
GET_QUERY_OPERATOR_STATS (TableScan pruning) and QUERY_HISTORY_BY_SESSION.BYTES_SCANNED, as in spike S5.
"""
import os
import time

import pytest

from helpers.macros import run_macro, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
SINCE = "2026-10-31T08:00:00.000000Z"


def quoted(*parts: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def rel(identifier: str, quoted_identifier: bool = False) -> dict:
    return {"database": DATABASE, "schema": SCHEMA, "identifier": identifier, "quoted": quoted_identifier}


def utc_now(sf) -> str:
    return sf.cursor().execute("""select to_varchar(sysdate(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"')""").fetchone()[0]


def insert_rows(sf, table: str, n: int, nulls: int = 0, start: int = 0, wide: bool = False) -> None:
    """n rows; the first `nulls` have AMOUNT null. UPDATED_AT is the UTC wall clock (NTZ), LOADED_AT the instant (LTZ).
    wide: fill NOTE with a uuid so that 100,000 rows fill whole micro-partitions (the S5 layout)."""
    sf.cursor().execute(
        f"insert into {table} (id, amount, note, updated_at, loaded_at) "
        f"select {start} + seq4(), iff(seq4() < {nulls}, null, seq4() % 7), "
        + ("'uc2-' || uuid_string(), " if wide else "null, ")
        + "convert_timezone('UTC', current_timestamp())::timestamp_ntz, current_timestamp() "
        f"from table(generator(rowcount => {n}))"
    )


def create_table(sf, table: str, change_tracking: bool) -> None:
    sf.cursor().execute(
        f"create transient table {table} "
        "(id number, amount number, note varchar, updated_at timestamp_ntz, loaded_at timestamp_ltz)"
        + (" change_tracking = true" if change_tracking else "")
    )


def scan_stats(sf, sql: str) -> dict:
    cur = sf.cursor()
    cur.execute("alter session set use_cached_result = false")
    value = cur.execute(sql).fetchone()[0]
    qid = cur.sfqid
    scanned, total = cur.execute(
        "select sum(operator_statistics:pruning:partitions_scanned::number), "
        "sum(operator_statistics:pruning:partitions_total::number) "
        f"from table(get_query_operator_stats('{qid}')) where operator_type = 'TableScan'"
    ).fetchone()
    bytes_scanned = cur.execute(
        "select bytes_scanned from table(information_schema.query_history_by_session(result_limit => 50)) "
        f"where query_id = '{qid}'"
    ).fetchone()[0]
    return {"value": value, "partitions_scanned": scanned, "partitions_total": total, "bytes_scanned": bytes_scanned}


# --- offline: rendering only, no connection ------------------------------------------------------

def render(window: dict, column: str = "amount", metrics=None, identifier: str = "fct_orders", q: bool = False) -> dict:
    args = {"rel": rel(identifier, q), "column_name": column, "window": window}
    if metrics is not None:
        args["metrics"] = metrics
    return run_macro("it_column_metrics_sql", args)


def test_changes_window_reads_only_inserted_row_versions_of_the_column():
    out = render({"mode": "changes", "since": SINCE})
    sql = " ".join(out["metrics_sql"].split()).lower()
    assert out["changes_source"] in out["metrics_sql"]
    assert "changes (information => default)" in sql
    assert f"at (timestamp => to_timestamp_tz('{SINCE.lower()}'))" in sql
    assert "metadata$action = 'insert'" in sql
    assert 'select "amount" from' in sql  # only the monitored column, never select *
    assert "'row_count'" in sql and "'null_rate'" in sql


def test_row_delta_and_column_metrics_share_one_changes_source():
    # get_row_count_changes (rows_added) and the column metrics read CHANGES through the same rendering
    out = render({"mode": "changes", "since": SINCE})
    assert out["changes_source"] in out["row_delta_sql"]
    assert "count_if(metadata$action = 'INSERT') - count_if(metadata$action = 'DELETE')" in out["row_delta_sql"]


@pytest.mark.parametrize("ts_type,cast", [
    ("TIMESTAMP_NTZ", "to_timestamp_ntz"),
    ("TIMESTAMP_LTZ", "to_timestamp_tz"),
    ("TIMESTAMP_TZ", "to_timestamp_tz"),
    ("DATE", "to_timestamp_ntz"),  # then ::date
])
def test_timestamp_window_compares_in_the_column_type(ts_type, cast):
    out = render({"mode": "timestamp_column", "since": SINCE, "timestamp_column": "updated_at", "timestamp_type": ts_type})
    sql = " ".join(out["metrics_sql"].split())
    assert "changes (" not in sql.lower()
    op = ">=" if ts_type == "DATE" else ">"
    assert f'"UPDATED_AT" {op} {cast}(\'{SINCE}\')' in sql, sql


def test_column_and_since_are_quoted_and_escaped():
    out = render({"mode": "changes", "since": "2026-10-31' or 1=1 --"}, column='Net "Amount"', identifier="Mixed Case", q=True)
    sql = out["metrics_sql"]
    assert '"Net ""Amount"""' in sql
    assert "to_timestamp_tz('2026-10-31\\' or 1=1 --')" in sql
    assert '"ICECORE_SANDBOX"."SPIKES"."Mixed Case"' in sql


def test_numeric_metrics_render_avg_min_max():
    sql = " ".join(render({"mode": "changes", "since": SINCE}, metrics=["avg", "min", "max"])["metrics_sql"].split())
    assert """'avg', avg("AMOUNT")::float""" in sql
    assert """'min', min("AMOUNT")::float""" in sql
    assert """'max', max("AMOUNT")::float""" in sql
    assert "'null_rate'" not in sql and "'row_count'" not in sql


def test_metrics_argument_selects_what_is_rendered():
    sql = render({"mode": "changes", "since": SINCE}, metrics=["null_rate"])["metrics_sql"]
    assert "'null_rate'" in sql and "'row_count'" not in sql and "avg(" not in sql


# --- sandbox --------------------------------------------------------------------------------------

def window_metrics_and_scans(sf, name: str, since: str) -> tuple[dict, dict, dict]:
    table = quoted(DATABASE, SCHEMA, name)
    got = run_macro("it_column_metrics", {"rel": rel(name.lower()), "column_name": "amount", "since": since})
    sql = run_macro("it_column_metrics_sql", {"rel": rel(name.lower()), "column_name": "amount", "window": got["window"]})["metrics_sql"]
    return got, scan_stats(sf, sql), scan_stats(sf, f"select count_if(amount is null) from {table} where id >= 0")


@pytest.mark.sandbox
def test_insert_only_window_reads_only_the_new_micro_partition(dbt, sf, run_id):
    # S5 layout: ten 100,000-row inserts with a uuid NOTE, about ten full micro-partitions before since
    name = f"UC2_COLS_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=True)
    try:
        for batch in range(10):
            insert_rows(sf, table, 100_000, start=batch * 100_000, wide=True)
        time.sleep(2)
        since = utc_now(sf)
        insert_rows(sf, table, 50, nulls=10, start=10_000_000, wide=True)

        got, changes, full = window_metrics_and_scans(sf, name, since)
        print("insert-only window, full partitions: changes", changes, "full scan", full)
        assert got["window"]["mode"] == "changes", got
        assert got["metrics"] == {"row_count": 50, "null_rate": 0.2}, got
        assert changes["partitions_scanned"] == 1 and full["partitions_scanned"] > 1, (changes, full)
        assert changes["bytes_scanned"] * 100 < full["bytes_scanned"], (changes, full)
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_small_insert_into_a_partial_micro_partition_is_still_correct(dbt, sf, run_id):
    # Found while writing this story: on a narrow table Snowflake packs 500,000 rows into 2 micro-partitions and
    # writes a 50-row insert by rewriting the last, partly filled one, so CHANGES reads its old and new versions.
    # An insert-only window is then not limited to the new rows. Values must be right; the cost is printed.
    name = f"UC2_COLS_NARROW_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=True)
    try:
        for batch in range(5):
            insert_rows(sf, table, 100_000, start=batch * 100_000)
        time.sleep(2)
        since = utc_now(sf)
        insert_rows(sf, table, 50, nulls=10, start=10_000_000)

        got, changes, full = window_metrics_and_scans(sf, name, since)
        print("insert-only window, partial partition: changes", changes, "full scan", full)
        assert got["window"]["mode"] == "changes", got
        assert got["metrics"] == {"row_count": 50, "null_rate": 0.2}, got
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_update_window_measures_new_row_versions(dbt, sf, run_id):
    # S5: after an UPDATE / DELETE / MERGE, CHANGES reads old and new versions of every rewritten micro-partition
    # and can cost more than a full scan. The values must still be right; the cost is printed for the PR.
    name = f"UC2_COLS_UPD_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=True)
    try:
        for batch in range(10):
            insert_rows(sf, table, 100_000, start=batch * 100_000, wide=True)
        time.sleep(2)
        since = utc_now(sf)
        sf.cursor().execute(f"update {table} set amount = null where id % 100000 = 1")  # 10 rows, one per batch

        got, changes, full = window_metrics_and_scans(sf, name, since)
        print("update window: changes", changes, "full scan", full)
        assert got["window"]["mode"] == "changes", got
        assert got["metrics"] == {"row_count": 10, "null_rate": 1.0}, got
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
@pytest.mark.parametrize("ts_column", ["updated_at", "loaded_at"])
def test_change_tracking_off_falls_back_to_the_timestamp_column(dbt, sf, run_id, ts_column):
    name = f"UC2_COLS_TS_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=False)
    try:
        insert_rows(sf, table, 30)
        sf.cursor().execute(  # rows from before since: outside the window
            f"update {table} set updated_at = dateadd(hour, -2, updated_at), loaded_at = dateadd(hour, -2, loaded_at)")
        time.sleep(2)
        since = utc_now(sf)
        time.sleep(1)
        insert_rows(sf, table, 50, nulls=10, start=100)

        got, logs = run_macro_with_logs(
            "it_column_metrics", {"rel": rel(name.lower()), "column_name": "amount", "since": since, "timestamp_column": ts_column})
        assert got["window"]["mode"] == "timestamp_column", got
        assert got["window"]["timestamp_column"] == ts_column
        assert got["metrics"] == {"row_count": 50, "null_rate": 0.2}, got
        assert any("using timestamp column" in m for m in logs), logs
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_no_window_returns_none_without_failing(dbt, sf, run_id):
    name = f"UC2_COLS_NONE_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=False)
    try:
        since = utc_now(sf)
        insert_rows(sf, table, 10)
        got, logs = run_macro_with_logs("it_column_metrics", {"rel": rel(name.lower()), "column_name": "amount", "since": since})
        assert got == {"window": None, "metrics": None}
        assert any("no change window" in m and "Change tracking is not enabled" in m for m in logs), logs

        # a missing column fails safely too
        got, logs = run_macro_with_logs("it_column_metrics", {
            "rel": rel(name.lower()), "column_name": "no_such_column", "since": since, "timestamp_column": "updated_at"})
        assert got["window"]["mode"] == "timestamp_column" and got["metrics"] is None, got
        assert any("compute_column_metrics could not read" in m for m in logs), logs
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_null_rate_keeps_full_float_precision(dbt, sf, run_id):
    # NUMBER division in Snowflake stops at scale 6 (1/3 -> 0.333333); the rate is computed in FLOAT
    name = f"UC2_COLS_THIRD_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=True)
    try:
        insert_rows(sf, table, 10)
        time.sleep(2)
        since = utc_now(sf)
        insert_rows(sf, table, 3, nulls=1, start=100)
        got = run_macro("it_column_metrics", {"rel": rel(name.lower()), "column_name": "amount", "since": since})
        assert got["metrics"]["null_rate"] == pytest.approx(1 / 3, rel=1e-12), got
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_empty_window_gives_zero_rows_and_no_null_rate(dbt, sf, run_id):
    name = f"UC2_COLS_EMPTY_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=True)
    try:
        insert_rows(sf, table, 10)
        time.sleep(2)
        got = run_macro("it_column_metrics", {"rel": rel(name.lower()), "column_name": "amount", "since": utc_now(sf)})
        assert got["window"]["mode"] == "changes"
        assert got["metrics"] == {"row_count": 0, "null_rate": None}, got
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


@pytest.mark.sandbox
def test_numeric_metrics_selection_and_non_numeric_column(dbt, sf, run_id):
    name = f"UC2_COLS_NUM_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    create_table(sf, table, change_tracking=True)
    try:
        insert_rows(sf, table, 10)
        time.sleep(2)
        since = utc_now(sf)
        sf.cursor().execute(f"insert into {table} (id, amount, note) values (1, 1, 'a'), (2, 2, null), (3, 3, 'c')")
        r = rel(name.lower())

        got = run_macro("it_column_metrics", {"rel": r, "column_name": "amount", "since": since,
                                              "metrics": ["row_count", "null_rate", "avg", "min", "max"]})
        assert got["metrics"] == {"row_count": 3, "null_rate": 0.0, "avg": 2.0, "min": 1.0, "max": 3.0}, got

        got, logs = run_macro_with_logs("it_column_metrics", {"rel": r, "column_name": "note", "since": since,
                                                              "metrics": ["null_rate", "avg", "max"]})
        assert got["metrics"] == {"null_rate": pytest.approx(1 / 3)}, got
        assert any("avg, max skipped" in m and "not numeric" in m for m in logs), logs

        got = run_macro("it_column_metrics", {"rel": r, "column_name": "amount", "since": since, "metrics": ["null_rate"]})
        assert got["metrics"] == {"null_rate": 0.0}, got
    finally:
        sf.cursor().execute(f"drop table if exists {table}")
