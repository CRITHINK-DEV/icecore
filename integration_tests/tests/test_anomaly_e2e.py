"""UC3 end to end: the generic monitor tests score their metrics through `dbt build` (tests/generic/*.sql ->
icecore.score_monitor_metrics -> icecore.score / native engine -> emit_metric `anomaly=`).

History is seeded as METRICS rows. emit_metric stamps events with now (a 30-day series cannot be written through
the emitter), so the obs schema ICECORE_OBS_<run_id> gets the real views and then a METRICS view that is the real one
UNION ALL a SEEDED view of 30 daily points per series; the tests read that history through the lag-aware reader and
write their own verdicts through the real emitter, so the verdict rows are real event-table records read back through
the view. Cases (fixtures in models/monitors/_anomaly_e2e.yml):
  spike  100 rows of history, 1000 rows now (10x), avg(amount) 10 in history, 500 now, freshness lag 1 h in history, seconds now
  calm   100 rows of history, 100 now (in the band)
  young  3 history points only (insufficient history)
The schema is dropped at the end; the tables are dropped too. Offline tests: test_score_monitor.py.
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, invoke, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = "SPIKES"
NOW = "convert_timezone('UTC', current_timestamp())::timestamp_ntz"


def quoted(table: str) -> str:
    return f'"{DATABASE}"."{SCHEMA}"."{table}"'


def statuses(res) -> dict:
    return {r.node.name: r.status.value for r in res.result.results}


@pytest.fixture(scope="module")
def e2e(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    spike, calm, young = (f"IT_UC3_{k}_{run_id}".upper() for k in ("SPIKE", "CALM", "YOUNG"))
    names = {"it_uc3_spike_table": spike, "it_uc3_calm_table": calm, "it_uc3_young_table": young}
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        base_vars = {"icecore": {"obs_schema": schema}, **names}
        obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
        run_macro("it_create_emit_procedures", {}, obs_vars)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        cur.execute(f'create table {quoted(spike)} (id number, amount number, country varchar, loaded_at timestamp_ntz)')
        cur.execute(f"insert into {quoted(spike)} select seq4(), 500, iff(mod(seq4(), 2) = 0, 'FR', 'DE'), sysdate() "
                    f"from table(generator(rowcount => 1000))")  # 10x the 100 rows of its history
        for table in (calm, young):
            cur.execute(f'create table {quoted(table)} (id number)')
            cur.execute(f"insert into {quoted(table)} select seq4() from table(generator(rowcount => 100))")
        qs = f'"{DATABASE}"."{schema}"'
        # 30 daily points per series: base + (-2..2). young has 3 points. The series start 1 h before each whole day back.
        cur.execute(f"""create view {qs}."SEEDED" as
            select s.relation, s.col as "COLUMN", null::varchar as dimension, s.metric,
                   dateadd(hour, -1, dateadd(day, -n.n, {NOW})) as observed_at,
                   (s.base + mod(n.n, 5) - 2)::float as value,
                   null::varchar as anomaly_engine, null::float as anomaly_expected, null::float as anomaly_lower,
                   null::float as anomaly_upper, null::float as anomaly_score, null::boolean as anomaly_is_anomaly,
                   null::varchar as invocation_id, null::varchar as node_unique_id, null::varchar as emit_query_id
            from (select seq4() as n from table(generator(rowcount => 30))) n
            join (select '{quoted(spike)}' as relation, 'row_count' as metric, null::varchar as col, 100 as base, 30 as points
                  union all select '{quoted(spike)}', 'avg', 'amount', 10, 30
                  union all select '{quoted(spike)}', 'freshness_lag_seconds', null, 3600, 30
                  union all select '{quoted(calm)}', 'row_count', null, 100, 30
                  union all select '{quoted(young)}', 'row_count', null, 100, 3) s on n.n < s.points""")
        real = call_macro("metrics_view_sql", obs_schema=schema)
        cur.execute(f'create or replace view {qs}."METRICS" as select * from ({real}) union all select * from {qs}."SEEDED"')
        since = cur.execute(f"select dateadd(minute, -2, {NOW})").fetchone()[0]

        # run 1: the project's default engine (auto, so native on a Snowflake without a model)
        fixtures = ["it_anom_volume_spike", "it_anom_volume_warn", "it_anom_volume_calm", "it_anom_volume_young",
                    "it_anom_column_spike", "it_anom_freshness_spike"]
        res = invoke(["build", "--select", " ".join(fixtures), "--vars", json.dumps(base_vars)])
        first = statuses(res)
        # run 2: the project var says cortex; one test overrides with native
        seen = []

        def collect(event) -> None:
            msg = getattr(event.info, "msg", "") or ""
            if "anomaly engine" in msg:
                seen.append(msg)

        cortex_vars = {"icecore": {"obs_schema": schema, "anomaly_engine": "cortex"}, **names}
        invoke(["build", "--select", "it_anom_volume_spike it_anom_volume_override", "--vars", json.dumps(cortex_vars)], [collect])
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=14, since=since, settle_s=10)
        cur_d = sf.cursor(DictCursor)
        metrics = [{k.lower(): v for k, v in r.items()} for r in cur_d.execute(
            f'select * from {qs}."METRICS" where invocation_id is not null and observed_at >= %(since)s',
            {"since": since}).fetchall()]
        anomalies = [{k.lower(): v for k, v in r.items()} for r in sf.cursor(DictCursor).execute(
            "select record:name::string as name, record_attributes as attrs from " + os.environ.get("ICECORE_EVENT_TABLE", "ICECORE_SANDBOX.TELEMETRY.EVENTS")
            + " where resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.anomaly' and TIMESTAMP >= %(since)s",
            {"schema": schema, "since": since}).fetchall()]
        yield {"first": first, "metrics": metrics, "anomalies": [json.loads(a["attrs"]) for a in anomalies],
               "spike": quoted(spike), "calm": quoted(calm), "young": quoted(young), "fallbacks": seen}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        for table in (spike, calm, young):
            cur.execute(f'drop table if exists {quoted(table)}')


def rows(e2e, table, metric, **where):
    return [m for m in e2e["metrics"] if m["relation"] == e2e[table] and m["metric"] == metric
            and all(m[k] == v for k, v in where.items())]


@pytest.mark.sandbox
def test_a_10x_spike_after_30_days_of_history_fails_the_volume_test_with_is_anomaly_true(e2e):
    assert e2e["first"]["it_anom_volume_spike"] == "fail"
    spike = [m for m in rows(e2e, "spike", "row_count") if m["value"] == 1000 and m["anomaly_engine"] == "native"]
    assert spike and all(m["anomaly_is_anomaly"] is True for m in spike)
    m = spike[0]
    assert m["anomaly_lower"] <= m["anomaly_expected"] <= m["anomaly_upper"] < 1000 and m["anomaly_score"] > 0


@pytest.mark.sandbox
def test_severity_warn_warns_and_a_value_in_the_band_passes_scored_not_anomalous(e2e):
    assert e2e["first"]["it_anom_volume_warn"] == "warn"
    assert e2e["first"]["it_anom_volume_calm"] == "pass"
    calm = rows(e2e, "calm", "row_count")
    assert calm and all(m["anomaly_is_anomaly"] is False and m["anomaly_engine"] == "native" for m in calm)


@pytest.mark.sandbox
def test_insufficient_history_passes_unscored(e2e):
    assert e2e["first"]["it_anom_volume_young"] == "pass"
    young = rows(e2e, "young", "row_count")
    assert young and all(m["anomaly_is_anomaly"] is None and m["anomaly_engine"] is None for m in young
                         if m["invocation_id"])


@pytest.mark.sandbox
def test_a_per_test_engine_override_scores_with_that_engine_and_the_var_does_not(e2e):
    # var engine cortex: the test without an override tries Cortex and falls back to native (logged); the override
    # test scores with native outright and logs no fallback
    fell_back = [m for m in e2e["fallbacks"] if "'cortex' scored row_count" in m]
    assert len(fell_back) == 1, e2e["fallbacks"]
    assert all(m["anomaly_engine"] == "native" for m in rows(e2e, "spike", "row_count") if m["value"] == 1000)


@pytest.mark.sandbox
def test_column_and_freshness_anomalies_fail_their_tests(e2e):
    assert e2e["first"]["it_anom_column_spike"] == "fail"
    avg = [m for m in rows(e2e, "spike", "avg") if m["column"] == "amount" and m["invocation_id"]]
    assert avg and avg[0]["anomaly_is_anomaly"] is True and avg[0]["value"] == 500
    assert e2e["first"]["it_anom_freshness_spike"] == "fail"  # max_lag 1d is not breached: the anomaly fails it
    lag = [m for m in rows(e2e, "spike", "freshness_lag_seconds") if m["invocation_id"]]
    assert lag and lag[0]["anomaly_is_anomaly"] is True and lag[0]["value"] < 3000


@pytest.mark.sandbox
def test_the_anomaly_record_carries_band_window_and_engine(e2e):
    mine = [a for a in e2e["anomalies"] if a.get("dbt.node.relation") == e2e["spike"] and a.get("dbt.metric.name") == "row_count"]
    assert mine
    a = mine[0]
    assert a["dbt.anomaly.engine"] == "native" and a["dbt.anomaly.is_anomaly"] is True
    assert a["dbt.anomaly.lower"] <= a["dbt.anomaly.expected"] <= a["dbt.anomaly.upper"]
    assert a["dbt.window.lookback_days"] == 30 and a["dbt.window.history_points"] >= 7 and a["dbt.window.history_days"] >= 29
