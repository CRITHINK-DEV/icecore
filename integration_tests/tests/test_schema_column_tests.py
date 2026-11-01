"""UC2 generic tests icecore.schema_changes and icecore.column_anomaly (tests/generic/schema_changes.sql,
column_anomaly.sql). Fixtures: integration_tests/models/monitors/_schema_column.yml (sources whose identifiers are vars).

schema_changes compares SHOW COLUMNS with the signature the previous run recorded (`icecore.schema_signature`), fails
or warns (severity) on a diff and writes one `icecore.schema_change` record per change. column_anomaly computes the
requested metrics over the changed rows, writes one metric each and checks static bounds; the scoring hook for UC3 is
not called. Sandbox tests create the emitter procedures in ICECORE_OBS_<run_id> (the obs_schema var), run `dbt test`,
and read records back through icecore.base_events().
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
EVENTS = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
          " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'")


def results_by_name(res) -> dict:
    return {r.node.name: r.status.value for r in res.result.results}


def quoted(table: str) -> str:
    return f'"{DATABASE}"."{SCHEMA}"."{table}"'


# --- offline: parse only, no connection -----------------------------------------------------------

def test_project_with_the_generic_tests_parses_and_registers_them():
    assert invoke(["deps"]).success
    res = invoke(["parse"])
    assert res.success
    nodes = {n.name: n for n in res.result.nodes.values() if n.resource_type.value == "test"}
    assert nodes["it_schema_changes_fail"].test_metadata.name == "schema_changes"
    assert nodes["it_schema_changes_warn"].config.severity.lower() == "warn"
    three = nodes["it_column_three_metrics"]
    assert three.test_metadata.name == "column_anomaly"
    assert three.test_metadata.kwargs["metrics"] == ["null_rate", "avg", "max"]
    assert three.test_metadata.kwargs["column_name"] == "amount"


@pytest.mark.parametrize("var_args", [
    {"it_bad_bounds": {"median": {"max": 1}}},         # metric not in metrics
    {"it_bad_bounds": {"avg": 5}},                     # limit is not {min, max}
    {"it_bad_bounds": {"avg": {"mean": 1}}},           # unknown limit key
    {"it_bad_bounds": {"avg": {}}},                    # no limit at all
    {"it_bad_bounds": "avg<5"},                        # not a mapping
    {"it_bad_bounds": {"avg": {"max": "ten"}}},        # limit is not a number
    {"it_bad_bounds": {"avg": {"min": True}}},         # nor a boolean
])
def test_invalid_bounds_fail_at_parse(var_args):
    assert invoke(["deps"]).success
    assert not invoke(["parse", "--vars", json.dumps(var_args)]).success


def test_history_reads_look_back_30_days_by_default_and_start_with_the_relation():
    assert call_macro("_icecore_history_lookback_seconds") == 30 * 86400
    rel = {"database": "icecore_sandbox", "schema": "spikes", "identifier": "orders"}
    bound = run_macro("it_history_bound", {"rel": rel})
    assert bound.startswith("node_relation = '")
    assert bound.index("node_relation") < bound.index("event_at >= dateadd(second, -2592000, sysdate())")


# --- sandbox: schema_changes ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def drift(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    fail, warn = f"IT_DRIFT_FAIL_{run_id}".upper(), f"IT_DRIFT_WARN_{run_id}".upper()
    stale = f"IT_DRIFT_STALE_{run_id}".upper()
    names = {"it_uc2_drift_fail_table": fail, "it_uc2_drift_warn_table": warn, "it_uc2_drift_stale_table": stale}
    obs = {"icecore": {"obs_schema": schema}, **names}
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps({"icecore": {"obs_schema": schema}})])
        for table in (fail, warn, stale):
            cur.execute(f'create table {quoted(table)} (id number, note varchar(20), gone date)')
        since = cur.execute("select dateadd(minute, -2, sysdate())").fetchone()[0]
        select = ["--select", "it_schema_changes_fail", "it_schema_changes_warn", "it_schema_changes_stale"]
        first = results_by_name(invoke(["test", *select, "--vars", json.dumps(obs)]))
        # the baselines are in the event table before the schema moves
        wait_for_events(sf, EVENTS + " and record:name::string = 'icecore.schema_signature'",
                        {"db": DATABASE, "schema": schema}, min_rows=3, since=since)
        cur.execute(f'alter table {quoted(fail)} drop column gone')
        cur.execute(f'alter table {quoted(fail)} add column extra boolean')
        cur.execute(f'alter table {quoted(fail)} alter column note set data type varchar(50)')
        cur.execute(f'alter table {quoted(warn)} drop column gone')
        second = results_by_name(invoke(["test", *select, "--vars", json.dumps(obs)]))
        # the baseline of `stale` is now older than a 2 second look back: that run counts as the first
        cur.execute(f'alter table {quoted(stale)} drop column gone')
        short = {**obs, "icecore": {"obs_schema": schema, "baseline_lookback_days": 0.00002}}
        third = results_by_name(invoke(["test", "--select", "it_schema_changes_stale", "--vars", json.dumps(short)]))
        raw = wait_for_events(sf, EVENTS, {"db": DATABASE, "schema": schema}, min_rows=9, since=since, settle_s=10)
        view = f'"{DATABASE}"."{schema}"."BASE_EVENTS"'
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        rows = sf.cursor(DictCursor).execute(
            f"select * from {view} where event_at >= %(since)s", {"since": since}).fetchall()
        yield {"fail": fail, "warn": warn, "stale": stale, "first": first, "second": second, "third": third, "raw": raw,
               "rows": [{k.lower(): v for k, v in r.items()} for r in rows]}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        for table in (fail, warn, stale):
            cur.execute(f"drop table if exists {quoted(table)}")


def changes_of(d: dict, table: str) -> dict:
    out = {}
    for r in d["rows"]:
        if r["node_relation"] == quoted(table) and r["record_name"] == "icecore.schema_change":
            a = json.loads(r["attributes"])
            out[a["dbt.schema.column"]] = a
    return out


@pytest.mark.sandbox
def test_first_run_passes_and_records_the_baseline(drift):
    assert drift["first"] == {"it_schema_changes_fail": "pass", "it_schema_changes_warn": "pass",
                              "it_schema_changes_stale": "pass"}
    sigs = [r for r in drift["rows"] if r["record_name"] == "icecore.schema_signature"]
    assert len([r for r in sigs if r["node_relation"] == quoted(drift["fail"])]) == 2  # baseline + after the change


@pytest.mark.sandbox
def test_a_removed_column_fails_and_a_schema_changes_record_lands(drift):
    assert drift["second"]["it_schema_changes_fail"] == "fail"
    changes = changes_of(drift, drift["fail"])
    assert changes["GONE"]["dbt.schema.change_kind"] == "removed"
    assert changes["GONE"]["dbt.schema.from_type"] == "DATE"
    assert changes["EXTRA"]["dbt.schema.change_kind"] == "added" and changes["EXTRA"]["dbt.schema.to_type"] == "BOOLEAN"
    assert changes["NOTE"]["dbt.schema.change_kind"] == "type_changed"
    assert (changes["NOTE"]["dbt.schema.from_type"], changes["NOTE"]["dbt.schema.to_type"]) == (
        "VARCHAR(20)", "VARCHAR(50)")
    assert all(a["icecore.level"] == "warn" and a["dbt.invocation_id"] for a in changes.values())


@pytest.mark.sandbox
def test_a_baseline_older_than_the_lookback_counts_as_the_first_run(drift):
    # `stale` lost a column, but its baseline is outside baseline_lookback_days: pass, no change record
    assert drift["third"] == {"it_schema_changes_stale": "pass"}
    assert drift["second"]["it_schema_changes_fail"] == "fail"
    assert changes_of(drift, drift["stale"]) == {}


@pytest.mark.sandbox
def test_severity_warn_warns_instead_of_failing(drift):
    assert drift["second"]["it_schema_changes_warn"] == "warn"
    assert set(changes_of(drift, drift["warn"])) == {"GONE"}


# --- sandbox: column_anomaly ----------------------------------------------------------------------

N_ROWS, N_NULL = 100, 20  # amount = id, null for the first 20 ids: null_rate 0.2, avg 59.5, max 99


@pytest.fixture(scope="module")
def columns(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    table = f"IT_COLUMNS_{run_id}".upper()
    obs = {"icecore": {"obs_schema": schema}}
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps(obs)])
        cur.execute(f'create table {quoted(table)} (id number, amount number(10, 2)) change_tracking = true')
        time.sleep(3)
        since_ts = cur.execute("select sysdate()").fetchone()[0]
        since = since_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        time.sleep(2)
        cur.execute(f'insert into {quoted(table)} select seq4(), iff(seq4() < {N_NULL}, null, seq4()) '
                    f'from table(generator(rowcount => {N_ROWS}))')
        log_since = cur.execute("select dateadd(minute, -3, sysdate())").fetchone()[0]
        variables = {**obs, "it_uc2_column_table": table, "it_since": since}
        first = results_by_name(invoke(["test", "--select", "it_column_three_metrics", "it_column_bounds_ok",
                                        "it_column_bounds_breached", "--vars", json.dumps(variables)]))
        wait_for_events(sf, EVENTS + " and record:name::string = 'icecore.metric'", {"db": DATABASE, "schema": schema},
                        min_rows=6, since=log_since)
        cur.execute(f'insert into {quoted(table)} select 1000 + seq4(), seq4() from table(generator(rowcount => 10))')
        # no `since`: the window starts at the previous observation of the column
        second = results_by_name(invoke(["test", "--select", "it_column_from_history", "--vars",
                                         json.dumps({**obs, "it_uc2_column_table": table})]))
        raw = wait_for_events(sf, EVENTS + " and record:name::string = 'icecore.metric'",
                              {"db": DATABASE, "schema": schema}, min_rows=7, since=log_since, settle_s=10)
        view = f'"{DATABASE}"."{schema}"."BASE_EVENTS"'
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        rows = sf.cursor(DictCursor).execute(
            f"select * from {view} where event_at >= %(since)s", {"since": log_since}).fetchall()
        yield {"table": table, "first": first, "second": second, "raw": raw,
               "rows": [{k.lower(): v for k, v in r.items()} for r in rows]}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        cur.execute(f"drop table if exists {quoted(table)}")


def metric_rows(c: dict) -> list:
    return [r for r in c["rows"] if r["node_relation"] == quoted(c["table"]) and r["record_name"] == "icecore.metric"]


@pytest.mark.sandbox
def test_three_metrics_land_with_their_column(columns):
    assert columns["first"]["it_column_three_metrics"] == "pass"
    rows = metric_rows(columns)
    got = {r["metric_name"] for r in rows if r["metric_column"] == "amount"}
    assert {"null_rate", "avg", "max"} <= got
    by_name = {r["metric_name"]: r["metric_value"] for r in rows if r["metric_name"] in ("null_rate", "avg", "max")}
    assert by_name["null_rate"] == pytest.approx(N_NULL / N_ROWS)
    assert by_name["avg"] == pytest.approx(59.5) and by_name["max"] == 99.0


@pytest.mark.sandbox
def test_every_metric_records_the_window_end_and_the_next_window_starts_there(columns):
    rows = metric_rows(columns)
    until = {json.loads(r["attributes"])["dbt.window.until"] for r in rows if r["metric_name"] != "window_rows"}
    assert until and all(u.endswith("Z") for u in until)  # taken before the read, one per test run
    (history,) = [r for r in rows if r["metric_name"] == "window_rows"]
    assert json.loads(history["attributes"])["dbt.window.until"] > max(until)  # and the second run's end is later


@pytest.mark.sandbox
def test_static_bounds_decide_the_verdict(columns):
    assert columns["first"]["it_column_bounds_ok"] == "pass"
    assert columns["first"]["it_column_bounds_breached"] == "fail"  # avg 59.5 above max 10, the metric still lands


@pytest.mark.sandbox
def test_each_metric_is_one_record_per_test_run(columns):
    assert len(metric_rows(columns)) == 3 + 2 + 1 + 1  # three_metrics, bounds_ok, bounds_breached, from_history


@pytest.mark.sandbox
def test_window_starts_at_the_previous_observation(columns):
    assert columns["second"]["it_column_from_history"] == "pass"
    (rows,) = [[r for r in metric_rows(columns) if r["metric_name"] == "window_rows"]]
    assert [r["metric_value"] for r in rows] == [10.0]  # only the 10 rows added after the first observation
