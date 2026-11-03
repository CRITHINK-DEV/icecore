"""UC2 generic tests icecore.freshness and icecore.volume (tests/generic/freshness.sql, volume.sql).

Fixtures: integration_tests/models/monitors/_freshness_volume.yml (sources whose identifiers are vars). Each test
computes from metadata (zero scan), emits one metric through icecore.emit_metric and compares with the thresholds it
is given; they are scored against their history (tests/test_anomaly_e2e.py). The sandbox tests create the emitter procedures in a schema of their
own, ICECORE_OBS_<run_id> (the obs_schema var), run `dbt test`, and read the metrics back through icecore.base_events().
The story's "changed 3 hours ago" is exercised as a 5 second max_lag on a table left to age 10 seconds: a table's
last change time cannot be backdated, and the comparison is the same.
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, invoke, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = "SPIKES"
N_ROWS = 100
AGE_S = 10
ALL_TESTS = ("it_freshness_bare", "it_freshness_pass", "it_freshness_fail", "it_freshness_warn", "it_volume_bare",
             "it_volume_in_range", "it_volume_too_few", "it_volume_too_many", "it_freshness_unmeasured_bare", "it_freshness_unmeasured_max_lag",
             "it_volume_unmeasured_bare", "it_volume_unmeasured_min_rows", "it_volume_unmeasured_warn")


def results_by_name(res) -> dict:
    return {r.node.name: r.status.value for r in res.result.results}


# --- offline: parse only, no connection -----------------------------------------------------------

def test_project_with_the_generic_tests_parses_and_registers_them():
    assert invoke(["deps"]).success
    res = invoke(["parse"])
    assert res.success
    nodes = {n.name: n for n in res.result.nodes.values() if n.resource_type.value == "test"}
    for name in ALL_TESTS:
        assert name in nodes, name
    assert nodes["it_freshness_fail"].test_metadata.name == "freshness"
    assert nodes["it_freshness_fail"].test_metadata.kwargs["max_lag"] == "0.001h"
    assert nodes["it_volume_too_few"].test_metadata.kwargs["min_rows"] == 500


@pytest.mark.parametrize("name,var_args", [
    ("max_lag", {"it_max_lag": "soon"}),
    ("max_lag", {"it_max_lag": "2 weeks"}),
    ("max_lag", {"it_max_lag": -5}),
    ("max_lag", {"it_max_lag": True}),
    ("min_rows", {"it_min_rows": 5000, "it_max_rows": 10}),
    ("min_rows", {"it_min_rows": -1}),
])
def test_invalid_thresholds_fail_at_parse(name, var_args):
    assert invoke(["deps"]).success
    assert not invoke(["parse", "--vars", json.dumps(var_args)]).success


@pytest.mark.parametrize("value", ["30s", "2h", "1d", "1.5h", " 45 m ", 90])
def test_valid_max_lag_forms_parse(value):
    assert invoke(["deps"]).success
    assert invoke(["parse", "--vars", json.dumps({"it_max_lag": value})]).success


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def monitored(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    fresh, vol = f"IT_FRESH_{run_id}".upper(), f"IT_VOL_{run_id}".upper()
    empty, src_view = f"IT_EMPTY_{run_id}".upper(), f"IT_VIEW_{run_id}".upper()
    names = {"it_uc2_fresh_table": fresh, "it_uc2_vol_table": vol, "it_uc2_empty_table": empty, "it_uc2_view": src_view}
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        for table in (fresh, vol):
            cur.execute(f'create table "{DATABASE}"."{SCHEMA}"."{table}" (id number, note varchar)')
            cur.execute(f'insert into "{DATABASE}"."{SCHEMA}"."{table}" select seq4(), \'uc2\' '
                        f'from table(generator(rowcount => {N_ROWS}))')
        cur.execute(f'create table "{DATABASE}"."{SCHEMA}"."{empty}" (id number)')
        cur.execute(f'create view "{DATABASE}"."{SCHEMA}"."{src_view}" as select * from "{DATABASE}"."{SCHEMA}"."{vol}"')
        time.sleep(AGE_S)  # the freshness table is now older than the 5 s of it_freshness_fail
        since = cur.execute("select dateadd(minute, -2, sysdate())").fetchone()[0]
        started = cur.execute("select dateadd(second, -5, current_timestamp())").fetchone()[0]
        obs_vars = {"icecore": {"obs_schema": schema}, **names}
        res = invoke(["test", "--select", "source:it_uc2_monitors", "--exclude", "it_volume_no_procedures",
                      "--vars", json.dumps(obs_vars)])
        statuses = results_by_name(res)
        # same tests with no procedures in the obs schema: the telemetry CALL fails, the verdict must not change
        missing = {"icecore": {"obs_schema": f"{schema}_MISSING"}, **names}
        res_missing = invoke(["test", "--select", "it_volume_no_procedures", "--vars", json.dumps(missing)])
        raw = wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=7, since=since, settle_s=10)
        view = f'"{DATABASE}"."{schema}"."BASE_EVENTS"'
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        rows = sf.cursor(DictCursor).execute(
            f"select * from {view} where event_at >= %(since)s", {"since": since}).fetchall()
        history = sf.cursor(DictCursor).execute(
            "select query_text, query_type, bytes_scanned from table(information_schema."
            "query_history_by_user(user_name => current_user(), end_time_range_start => %(t)s::timestamp_ltz, "
            "result_limit => 10000))", {"t": started}).fetchall()
        yield {"run_id": run_id, "fresh": fresh, "vol": vol, "statuses": statuses, "raw": raw, "history": history,
               "missing": results_by_name(res_missing), "missing_ok": res_missing.success,
               "rows": [{k.lower(): v for k, v in r.items()} for r in rows]}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        cur.execute(f'drop view if exists "{DATABASE}"."{SCHEMA}"."{src_view}"')
        for table in (fresh, vol, empty):
            cur.execute(f'drop table if exists "{DATABASE}"."{SCHEMA}"."{table}"')


def metrics_of(m: dict, table: str, metric: str) -> list:
    rel = f'"{DATABASE}"."{SCHEMA}"."{table}"'
    return [r for r in m["rows"] if r["node_relation"] == rel and r["metric_name"] == metric]


@pytest.mark.sandbox
def test_max_lag_below_the_age_fails_and_a_freshness_metric_lands(monitored):
    s = monitored["statuses"]
    assert s["it_freshness_fail"] == "fail"
    assert s["it_freshness_pass"] == "pass"
    assert s["it_freshness_warn"] == "warn"  # severity warn: a breach warns instead of failing
    rows = metrics_of(monitored, monitored["fresh"], "freshness_lag_seconds")
    assert len(rows) >= 4  # bare, pass, fail, warn (it_freshness_var too)
    assert all(AGE_S <= r["metric_value"] < 600 for r in rows), [r["metric_value"] for r in rows]
    assert all(r["record_name"] == "icecore.metric" and r["invocation_id"] for r in rows)


@pytest.mark.sandbox
def test_unreadable_metric_passes_bare_and_hits_severity_with_thresholds(monitored):
    s = monitored["statuses"]
    assert s["it_freshness_unmeasured_bare"] == "pass"
    assert s["it_freshness_unmeasured_max_lag"] == "fail"  # one row `unmeasured: <reason>`
    assert s["it_volume_unmeasured_bare"] == "pass"
    assert s["it_volume_unmeasured_min_rows"] == "fail"
    assert s["it_volume_unmeasured_warn"] == "warn"


@pytest.mark.sandbox
def test_bare_freshness_passes_and_records_the_lag(monitored):
    assert monitored["statuses"]["it_freshness_bare"] == "pass"


@pytest.mark.sandbox
def test_bare_volume_passes_and_records_row_count(monitored):
    assert monitored["statuses"]["it_volume_bare"] == "pass"
    rows = metrics_of(monitored, monitored["vol"], "row_count")
    assert rows and all(r["metric_value"] == N_ROWS for r in rows)
    assert json.loads(rows[0]["attributes"])["dbt.metric.value"] == N_ROWS  # a JSON number


@pytest.mark.sandbox
def test_volume_thresholds(monitored):
    s = monitored["statuses"]
    assert s["it_volume_in_range"] == "pass"
    assert s["it_volume_too_few"] == "fail"
    assert s["it_volume_too_many"] == "fail"
    assert s["it_volume_var"] == "pass"


@pytest.mark.sandbox
def test_every_test_emitted_exactly_one_metric(monitored):
    # 5 freshness tests + 5 volume tests ran against the obs schema (the no_procedures one wrote elsewhere)
    assert len(metrics_of(monitored, monitored["fresh"], "freshness_lag_seconds")) == 5
    assert len(metrics_of(monitored, monitored["vol"], "row_count")) == 5


@pytest.mark.sandbox
def test_a_failed_telemetry_call_does_not_change_the_verdict(monitored):
    assert monitored["missing"] == {"it_volume_no_procedures": "pass"}
    assert monitored["missing_ok"]


@pytest.mark.sandbox
def test_no_full_scan_of_the_monitored_tables(monitored):
    import re
    for table in (monitored["fresh"], monitored["vol"]):
        mine = [h for h in monitored["history"]
                if table.lower() in h["QUERY_TEXT"].lower() and "query_history" not in h["QUERY_TEXT"].lower()
                and h["QUERY_TYPE"] != "INSERT" and not h["QUERY_TEXT"].lower().startswith(("create", "drop"))]
        assert mine, f"the test queries about {table} were not found in the query history"
        for h in mine:
            text = h["QUERY_TEXT"].lower()
            assert not re.search(rf'\bfrom\s+"?{DATABASE.lower()}"?\."?{SCHEMA.lower()}"?\."?{table.lower()}"?\s*($|\)|where|limit)', text), text
            if "information_schema.tables" not in text:  # that read scans the metadata view, a few hundred bytes
                assert not h["BYTES_SCANNED"], (text, h["BYTES_SCANNED"])
