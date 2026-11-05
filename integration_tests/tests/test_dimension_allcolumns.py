"""UC2 generic tests icecore.dimension_anomaly and icecore.all_columns_anomaly (tests/generic/dimension_anomaly.sql,
all_columns_anomaly.sql). Fixtures: integration_tests/models/monitors/_dimension_allcolumns.yml (sources whose
identifiers are vars).

dimension_anomaly writes one `window_rows` metric per value of the tested column (dimension `<column>=<value>`, the
rest grouped as other past max_values); all_columns_anomaly writes the default metrics for every column of the relation
but the excluded ones. Static bounds decide the verdict; the scoring hook for UC3 is not called. Sandbox tests create
the emitter procedures in ICECORE_OBS_<run_id> (the obs_schema var), run `dbt test`, and read the metrics back through
icecore.base_events().
"""
import json
import os
import time
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, install_deps, invoke, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = "SPIKES"
EVENTS = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
          " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'"
          " and record:name::string = 'icecore.metric'")
COLUMNS = {"ID", "COUNTRY", "AMOUNT", "NOTE", "LOADED_AT"}


def results_by_name(res) -> dict:
    return {r.node.name: r.status.value for r in res.result.results}


def quoted(table: str) -> str:
    return f'"{DATABASE}"."{SCHEMA}"."{table}"'


# --- offline: parse only, no connection -----------------------------------------------------------

def test_project_with_the_generic_tests_parses_and_registers_them():
    install_deps()
    res = invoke(["parse"])
    assert res.success
    nodes = {n.name: n for n in res.result.nodes.values() if n.resource_type.value == "test"}
    assert nodes["it_dimension_country"].test_metadata.name == "dimension_anomaly"
    assert nodes["it_dimension_country"].test_metadata.kwargs["column_name"] == "country"
    assert nodes["it_all_columns_excluded"].test_metadata.name == "all_columns_anomaly"
    assert nodes["it_all_columns_excluded"].test_metadata.kwargs["exclude_columns"] == ["note", "loaded_at"]


@pytest.mark.parametrize("bounds", [{"avg": 1}, {"max": 1, "mean": 2}, {}, "min>1"])
def test_invalid_dimension_bounds_fail_at_parse(bounds):
    install_deps()
    assert not invoke(["parse", "--vars", json.dumps({"it_bad_dim_bounds": bounds})]).success


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def monitored(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    tables = {key: f"IT_{key.upper()}_{run_id}".upper() for key in ("dim", "cap", "all", "excl")}
    obs = {"icecore": {"obs_schema": schema}}
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, ["--vars", json.dumps(obs)])
        for table in tables.values():
            cur.execute(f'create table {quoted(table)} (id number, country varchar, amount number(10, 2), note varchar, '
                        f'loaded_at timestamp_ntz) change_tracking = true')
        time.sleep(3)
        since = cur.execute("select sysdate()").fetchone()[0].strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        time.sleep(2)
        for table in tables.values():  # 60 FR, 30 DE, 10 ES; amount null for the first 20 ids
            cur.execute(f"insert into {quoted(table)} select seq4(), case when seq4() < 60 then 'FR' when seq4() < 90 "
                        f"then 'DE' else 'ES' end, iff(seq4() < 20, null, seq4()), 'x', sysdate() "
                        f"from table(generator(rowcount => 100))")
        log_since = cur.execute("select dateadd(minute, -3, sysdate())").fetchone()[0]
        variables = {**obs, "it_since": since, "it_uc2_dim_table": tables["dim"], "it_uc2_cap_table": tables["cap"],
                     "it_uc2_all_table": tables["all"], "it_uc2_excl_table": tables["excl"]}
        statuses = results_by_name(invoke(["test", "--select", "source:it_uc2_dimensions", "--exclude",
                                           "it_dimension_bad_bounds", "--vars", json.dumps(variables)]))
        # dim: 3, cap: 2, all: 10, excl: 6
        raw = wait_for_events(sf, EVENTS, {"db": DATABASE, "schema": schema}, min_rows=21, since=log_since, settle_s=10)
        view = f'"{DATABASE}"."{schema}"."BASE_EVENTS"'
        cur.execute(f"create view {view} as {call_macro('base_events', obs_schema=schema)}")
        rows = sf.cursor(DictCursor).execute(
            f"select * from {view} where event_at >= %(since)s", {"since": log_since}).fetchall()
        yield {"tables": tables, "statuses": statuses, "raw": raw,
               "rows": [{k.lower(): v for k, v in r.items()} for r in rows]}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        for table in tables.values():
            cur.execute(f"drop table if exists {quoted(table)}")


def metrics_of(m: dict, key: str) -> list:
    return [r for r in m["rows"] if r["node_relation"] == quoted(m["tables"][key]) and r["record_name"] == "icecore.metric"]


@pytest.mark.sandbox
def test_one_metric_per_country_value_lands(monitored):
    assert monitored["statuses"]["it_dimension_country"] == "pass"
    rows = metrics_of(monitored, "dim")
    got = {r["metric_dimension"]: r["metric_value"] for r in rows}
    assert got == {"country=FR": 60.0, "country=DE": 30.0, "country=ES": 10.0}
    assert all(r["metric_name"] == "window_rows" and r["metric_column"] == "country" for r in rows)


@pytest.mark.sandbox
def test_values_past_max_values_are_grouped_as_other_and_bounds_decide(monitored):
    got = {r["metric_dimension"]: r["metric_value"] for r in metrics_of(monitored, "cap")}
    assert got == {"country=FR": 60.0, "country=<other>": 40.0}
    assert monitored["statuses"]["it_dimension_capped"] == "pass"  # both counts are at least 20


@pytest.mark.sandbox
def test_default_metrics_land_for_all_five_columns(monitored):
    assert monitored["statuses"]["it_all_columns"] == "pass"
    rows = metrics_of(monitored, "all")
    got = {(r["metric_column"], r["metric_name"]): r["metric_value"] for r in rows}
    assert {c for c, _ in got} == COLUMNS
    assert {m for _, m in got} == {"window_rows", "null_rate"}
    assert len(rows) == 10 and all(r["metric_dimension"] is None for r in rows)
    assert all(got[(c, "window_rows")] == 100.0 for c in COLUMNS)
    assert got[("AMOUNT", "null_rate")] == pytest.approx(0.2) and got[("ID", "null_rate")] == 0.0


@pytest.mark.sandbox
def test_each_test_writes_its_metrics_in_one_call_and_tags_its_monitor(monitored):
    for key, monitor in (("dim", "dimension_anomaly"), ("cap", "dimension_anomaly"), ("all", "all_columns_anomaly"),
                         ("excl", "all_columns_anomaly")):
        rows = metrics_of(monitored, key)
        assert len({r["emit_query_id"] for r in rows}) == 1, key  # 3, 2, 10 and 6 metrics, one CALL each
        assert {json.loads(r["attributes"])["dbt.window.monitor"] for r in rows} == {monitor}


@pytest.mark.sandbox
def test_excluded_columns_are_skipped(monitored):
    assert monitored["statuses"]["it_all_columns_excluded"] == "pass"
    rows = metrics_of(monitored, "excl")
    assert {r["metric_column"] for r in rows} == {"ID", "COUNTRY", "AMOUNT"}
    assert len(rows) == 6
