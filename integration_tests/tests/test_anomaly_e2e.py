"""UC3 end to end: the generic monitor tests score their metrics through `dbt build` (tests/generic/*.sql ->
icecore.score_monitor_metrics -> native engine -> emit_metric `anomaly=`).

History is seeded as METRICS rows. emit_metric stamps events with now (a 30-day series cannot be written through
the emitter), so the obs schema ICECORE_OBS_<run_id> gets the real views and then a METRICS view that is the real one
UNION ALL a SEEDED view of 30 daily points per series. A series belongs to ONE test (node_unique_id): the tests read
only their own points. The verdicts are real event-table records read back through the view.
Cases (fixtures in models/monitors/_anomaly_e2e.yml): spike (100 rows of history, 1000 now; avg(amount) 10 -> 500; freshness
lag 1 h -> seconds), calm (in the band), young (3 history points). Severity: an anomaly alone is one failing row and WARNS by default (error_if >=2); a hard threshold is 2 rows and fails; var anomaly_severity error makes error_if >=1.
The schema is dropped at the end. Offline tests: test_score_monitor.py.
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
        ids = {n.name: n.unique_id for n in invoke(["parse"]).result.nodes.values() if n.resource_type.value == "test"}
        # (test, relation, metric, column, base value, points): 30 daily points of base + (-2..2); the test is the series' owner.
        # it_anom_volume_threshold has none on purpose: it shares the relation and metric of the others, and must not read theirs.
        series = [(t, spike, "row_count", None, 100, 30) for t in ("it_anom_volume_spike", "it_anom_volume_warn", "it_anom_volume_error",
                                                                   "it_anom_volume_override")]
        series += [("it_anom_column_spike", spike, "avg", "amount", 10, 30), ("it_anom_freshness_spike", spike, "freshness_lag_seconds", None, 3600, 30),
                   ("it_anom_volume_calm", calm, "row_count", None, 100, 30), ("it_anom_volume_young", young, "row_count", None, 100, 3)]
        series += [("it_anom_all_columns_spike", spike, m, c, 100, 30) for c in ("ID", "AMOUNT", "COUNTRY", "LOADED_AT") for m in ("window_rows", "null_rate")]
        lit = lambda v: "null::varchar" if v is None else "'" + str(v).replace("'", "''") + "'"
        rows_sql = " union all ".join(f"select {lit(ids[t])} as test_id, {lit(quoted(r))} as relation, {lit(m)} as metric, {lit(c)} as col, "
                                      f"{b} as base, {n} as points" for t, r, m, c, b, n in series)
        qs = f'"{DATABASE}"."{schema}"'
        cur.execute(f"""create view {qs}."SEEDED" as
            select s.relation, s.col as "COLUMN", null::varchar as dimension, s.metric,
                   dateadd(hour, -1, dateadd(day, -n.n, {NOW})) as observed_at,
                   (s.base + mod(n.n, 5) - 2)::float as value,
                   null::varchar as anomaly_engine, null::float as anomaly_expected, null::float as anomaly_lower,
                   null::float as anomaly_upper, null::float as anomaly_score, null::boolean as anomaly_is_anomaly,
                   null::varchar as invocation_id, s.test_id as node_unique_id, null::varchar as emit_query_id,
                   null::float as threshold
            from (select seq4() as n from table(generator(rowcount => 30))) n
            join ({rows_sql}) s on n.n < s.points""")
        real = call_macro("metrics_view_sql", obs_schema=schema)
        cur.execute(f'create or replace view {qs}."METRICS" as select * from ({real}) union all select * from {qs}."SEEDED"')
        since = cur.execute(f"select dateadd(minute, -2, {NOW})").fetchone()[0]

        def build(select, extra_vars=None, vars_=None):
            lines = []

            def collect(event) -> None:
                msg = getattr(event.info, "msg", "") or ""
                if "icecore: warning: anomaly in" in msg or "anomaly engine '" in msg:
                    lines.append(msg)

            icecore = {"obs_schema": schema, **(extra_vars or {})}
            res = invoke(["build", "--select", " ".join(select), "--vars", json.dumps({"icecore": icecore, **names})], [collect])
            return statuses(res), lines

        # run 1: defaults (anomaly_severity warn, engine auto: no Cortex model, so native)
        first, first_lines = build(["it_anom_volume_spike", "it_anom_volume_error", "it_anom_volume_threshold", "it_anom_volume_threshold_warn", "it_anom_volume_warn",
                                    "it_anom_volume_calm", "it_anom_volume_young", "it_anom_column_spike", "it_anom_freshness_spike"])
        # run 2: the var says cortex; the test volume_override scores with native outright
        _, second_lines = build(["it_anom_volume_spike", "it_anom_volume_override"], {"anomaly_engine": "cortex"})
        # run 3: anomaly_severity error
        third, _ = build(["it_anom_volume_spike", "it_anom_volume_warn", "it_anom_column_spike", "it_anom_freshness_spike"], {"anomaly_severity": "error"})
        # run 4: the wide test under cortex, 8 series of 30 days: Cortex is tried once for the relation
        _, wide_lines = build(["it_anom_all_columns_spike"], {"anomaly_engine": "cortex"})
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=26, since=since, settle_s=15)
        metrics = [{k.lower(): v for k, v in r.items()} for r in sf.cursor(DictCursor).execute(
            f'select * from {qs}."METRICS" where invocation_id is not null and observed_at >= %(since)s',
            {"since": since}).fetchall()]
        anomalies = [json.loads(r["ATTRS"]) for r in sf.cursor(DictCursor).execute(
            "select record_attributes as attrs from " + os.environ.get("ICECORE_EVENT_TABLE", "ICECORE_SANDBOX.TELEMETRY.EVENTS")
            + " where resource_attributes:\"snow.schema.name\"::string = %(schema)s and record:name::string = 'icecore.anomaly' and TIMESTAMP >= %(since)s",
            {"schema": schema, "since": since}).fetchall()]
        yield {"first": first, "third": third, "metrics": metrics, "anomalies": anomalies, "ids": ids,
               "spike": quoted(spike), "calm": quoted(calm), "young": quoted(young),
               "first_lines": first_lines, "second_lines": second_lines, "wide_lines": wide_lines}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        for table in (spike, calm, young):
            cur.execute(f'drop table if exists {quoted(table)}')


def rows(e2e, table, metric, test, **where):
    return [m for m in e2e["metrics"] if m["relation"] == e2e[table] and m["metric"] == metric and m["node_unique_id"] == e2e["ids"][test]
            and all(m[k] == v for k, v in where.items())]


@pytest.mark.sandbox
def test_a_10x_spike_after_30_days_of_history_is_scored_an_anomaly_and_the_test_warns_by_default(e2e):
    spike = rows(e2e, "spike", "row_count", "it_anom_volume_spike")
    assert spike and all(m["anomaly_is_anomaly"] is True and m["anomaly_engine"] == "native" and m["value"] == 1000 for m in spike)
    m = spike[0]
    assert m["anomaly_lower"] <= m["anomaly_expected"] <= m["anomaly_upper"] < 1000 and m["anomaly_score"] > 0
    assert e2e["first"]["it_anom_volume_spike"] == "warn"  # one failing row, error_if >=2: WARN, not fail
    assert e2e["first"]["it_anom_column_spike"] == "warn" and e2e["first"]["it_anom_freshness_spike"] == "warn"


@pytest.mark.sandbox
def test_the_anomaly_fails_the_test_with_var_anomaly_severity_error_or_the_projects_error_if(e2e):
    assert e2e["third"]["it_anom_volume_spike"] == "fail"  # var error: error_if >=1
    assert e2e["first"]["it_anom_volume_error"] == "fail"  # the project's error_if >=1 wins over the macro's, var left at warn
    assert e2e["third"]["it_anom_volume_warn"] == "warn"  # the project's severity warn wins: everything warns
    assert e2e["third"]["it_anom_column_spike"] == "fail" and e2e["third"]["it_anom_freshness_spike"] == "fail"


@pytest.mark.sandbox
def test_a_hard_threshold_still_fails_whatever_anomaly_severity_says_and_warns_with_severity_warn(e2e):
    assert e2e["first"]["it_anom_volume_threshold"] == "fail"  # min_rows 5000: 2 rows, error_if >=2
    assert e2e["first"]["it_anom_volume_threshold_warn"] == "warn"  # project severity warn on the test


@pytest.mark.sandbox
def test_a_test_reads_only_its_own_history_not_that_of_another_test_on_the_same_series(e2e):
    # volume_threshold measures the same relation and metric as volume_spike (30 seeded points) but has none of its own
    own = rows(e2e, "spike", "row_count", "it_anom_volume_threshold")
    assert own and all(m["anomaly_is_anomaly"] is None and m["anomaly_engine"] is None for m in own)


@pytest.mark.sandbox
def test_severity_warn_and_a_value_in_the_band_pass_scored_not_anomalous(e2e):
    assert e2e["first"]["it_anom_volume_warn"] == "warn" and e2e["first"]["it_anom_volume_calm"] == "pass"
    calm = rows(e2e, "calm", "row_count", "it_anom_volume_calm")
    assert calm and all(m["anomaly_is_anomaly"] is False and m["anomaly_engine"] == "native" for m in calm)


@pytest.mark.sandbox
def test_insufficient_history_passes_unscored(e2e):
    assert e2e["first"]["it_anom_volume_young"] == "pass"
    young = rows(e2e, "young", "row_count", "it_anom_volume_young")
    assert young and all(m["anomaly_is_anomaly"] is None and m["anomaly_engine"] is None for m in young)


@pytest.mark.sandbox
def test_a_per_test_engine_override_scores_with_that_engine_and_the_var_does_not(e2e):
    # var engine cortex: the test without an override tries Cortex and falls back to native (logged once); the override
    # test scores with native outright and logs no fallback
    fell_back = [m for m in e2e["second_lines"] if "'cortex' scored row_count" in m]
    assert len(fell_back) == 1, e2e["second_lines"]
    assert all(m["anomaly_engine"] == "native" for m in rows(e2e, "spike", "row_count", "it_anom_volume_override"))


@pytest.mark.sandbox
def test_cortex_is_tried_once_per_relation_not_once_per_metric(e2e):
    # 8 series of 30 days in the wide test: the first Cortex call fails (no model), the other 7 do not call
    assert len([m for m in e2e["wide_lines"] if "anomaly engine 'cortex'" in m]) == 1, e2e["wide_lines"]
    wide = [m for m in e2e["metrics"] if m["node_unique_id"] == e2e["ids"]["it_anom_all_columns_spike"]]
    assert len(wide) == 8 and all(m["anomaly_engine"] == "native" for m in wide)


@pytest.mark.sandbox
def test_column_and_freshness_anomalies_are_scored(e2e):
    avg = rows(e2e, "spike", "avg", "it_anom_column_spike", column="amount")
    assert avg and avg[0]["anomaly_is_anomaly"] is True and avg[0]["value"] == 500
    lag = rows(e2e, "spike", "freshness_lag_seconds", "it_anom_freshness_spike")
    assert lag and lag[0]["anomaly_is_anomaly"] is True and lag[0]["value"] < 3000


@pytest.mark.sandbox
def test_the_anomaly_record_carries_band_window_and_engine(e2e):
    mine = [a for a in e2e["anomalies"] if a.get("dbt.node.relation") == e2e["spike"] and a.get("dbt.metric.name") == "row_count"]
    assert mine
    a = mine[0]
    assert a["dbt.anomaly.engine"] == "native" and a["dbt.anomaly.is_anomaly"] is True
    assert a["dbt.anomaly.lower"] <= a["dbt.anomaly.expected"] <= a["dbt.anomaly.upper"]
    assert a["dbt.window.lookback_days"] == 30 and a["dbt.window.history_points"] >= 7 and a["dbt.window.history_days"] >= 29
