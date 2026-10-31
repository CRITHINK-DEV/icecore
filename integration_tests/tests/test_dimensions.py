"""UC2 row counts per dimension value: icecore.compute_dimension_metrics over the change window.

Called through the wrappers in integration_tests/macros/test_support/it_columns.sql.
"""
import json
import os
import time

import pytest

from helpers.macros import run_macro, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
SINCE = "2026-10-31T08:00:00.000000Z"


def quoted(*parts: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def rel(identifier: str) -> dict:
    return {"database": DATABASE, "schema": SCHEMA, "identifier": identifier, "quoted": False}


def utc_now(sf) -> str:
    return sf.cursor().execute("""select to_varchar(sysdate(), 'YYYY-MM-DD"T"HH24:MI:SS.FF6"Z"')""").fetchone()[0]


def insert_country(sf, table: str, country, n: int) -> None:
    value = "null" if country is None else f"'{country}'"
    sf.cursor().execute(f"insert into {table} (id, country) select seq4(), {value} from table(generator(rowcount => {n}))")


@pytest.fixture
def tracked_table(sf, run_id):
    name = f"UC2_DIM_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create transient table {table} (id number, country varchar) change_tracking = true")
    insert_country(sf, table, "US", 7)  # before since: outside the window
    time.sleep(2)
    try:
        yield name, table, utc_now(sf)
    finally:
        sf.cursor().execute(f"drop table if exists {table}")


# --- offline: rendering only, no connection ------------------------------------------------------

def test_dimension_sql_groups_beyond_the_cap_and_reads_only_the_column():
    sql = run_macro("it_dimension_metrics_sql", {"rel": rel("fct_orders"), "dimension_column": "country",
                                                 "window": {"mode": "changes", "since": SINCE}, "max_values": 3})
    flat = " ".join(sql.split()).lower()
    assert 'select "country" from' in flat  # never select *
    assert "changes (information => default)" in flat and "metadata$action = 'insert'" in flat
    assert "<= 3" in flat
    assert "group by" in flat


# --- sandbox --------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_counts_per_value(dbt, sf, tracked_table):
    name, table, since = tracked_table
    insert_country(sf, table, "FR", 10)
    insert_country(sf, table, "DE", 5)
    got = run_macro("it_dimension_metrics", {"rel": rel(name.lower()), "dimension_column": "country", "since": since})
    assert got["window"]["mode"] == "changes"
    assert got["dimensions"] == [
        {"value": "FR", "window_rows": 10, "other": False},
        {"value": "DE", "window_rows": 5, "other": False},
    ], got


@pytest.mark.sandbox
def test_values_beyond_the_cap_are_grouped_as_other(dbt, sf, tracked_table):
    name, table, since = tracked_table
    for country, n in [("FR", 10), ("DE", 5), ("IT", 3), ("ES", 2), (None, 4)]:
        insert_country(sf, table, country, n)
    got = run_macro("it_dimension_metrics", {"rel": rel(name.lower()), "dimension_column": "country", "since": since, "max_values": 2})
    assert got["dimensions"] == [
        {"value": "FR", "window_rows": 10, "other": False},
        {"value": "DE", "window_rows": 5, "other": False},
        {"value": None, "window_rows": 9, "other": True},  # IT 3 + null 4 + ES 2
    ], got

    # the cap also comes from vars: icecore: dimension_max_values; a null value is a value of its own
    switch = ["--vars", json.dumps({"icecore": {"dimension_max_values": 3}})]
    got, _ = run_macro_with_logs("it_dimension_metrics", {"rel": rel(name.lower()), "dimension_column": "country", "since": since}, switch)
    assert got["dimensions"] == [
        {"value": "FR", "window_rows": 10, "other": False},
        {"value": "DE", "window_rows": 5, "other": False},
        {"value": None, "window_rows": 4, "other": False},
        {"value": None, "window_rows": 5, "other": True},  # IT 3 + ES 2
    ], got


@pytest.mark.sandbox
def test_no_changed_rows_gives_an_empty_result(dbt, sf, tracked_table):
    name, _, since = tracked_table
    got, logs = run_macro_with_logs("it_dimension_metrics", {"rel": rel(name.lower()), "dimension_column": "country", "since": since})
    assert got == {"window": {"mode": "changes", "since": since}, "dimensions": []}, got
    assert not any("warning" in m for m in logs), logs


@pytest.mark.sandbox
def test_missing_dimension_column_returns_none_without_failing(dbt, sf, tracked_table):
    name, _, since = tracked_table
    got, logs = run_macro_with_logs("it_dimension_metrics", {"rel": rel(name.lower()), "dimension_column": "segment", "since": since})
    assert got["dimensions"] is None
    assert any("compute_dimension_metrics could not read" in m for m in logs), logs


@pytest.mark.sandbox
def test_table_recreated_after_since_falls_back_to_the_timestamp_column(dbt, sf, run_id):
    # same run-time fallback as the column metrics: CHANGES fails (000707), the timestamp column takes over
    name = f"UC2_DIM_RECREATED_{run_id}".upper()
    table = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(f"create transient table {table} (id number, country varchar, updated_at timestamp_ltz) change_tracking = true")
    try:
        sf.cursor().execute(f"insert into {table} select seq4(), 'US', current_timestamp() from table(generator(rowcount => 7))")
        time.sleep(2)
        since = utc_now(sf)
        time.sleep(1)
        sf.cursor().execute(
            f"create or replace transient table {table} change_tracking = true as "
            "select seq4() as id, iff(seq4() < 10, 'FR', 'DE') as country, current_timestamp() as updated_at "
            "from table(generator(rowcount => 15))")
        got, logs = run_macro_with_logs("it_dimension_metrics", {"rel": rel(name.lower()), "dimension_column": "country",
                                                                 "since": since, "timestamp_column": "updated_at"})
        assert got["dimensions"] == [
            {"value": "FR", "window_rows": 10, "other": False},
            {"value": "DE", "window_rows": 5, "other": False},
        ], got
        assert any("CHANGES query failed" in m and "using timestamp column updated_at" in m for m in logs), logs
    finally:
        sf.cursor().execute(f"drop table if exists {table}")
