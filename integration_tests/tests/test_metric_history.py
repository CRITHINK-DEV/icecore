"""UC3 lag-aware metric history reader: icecore.get_metric_history (macros/engines/get_metric_history.sql).

It reads one (relation, metric, column, dimension) series from the obs schema's METRICS view, in the shape
icecore.score takes (`observed_at`, `value`), over a lookback (default 30 days), leaving out points newer than the
history delay (var icecore.history_delay_minutes, default 1, spike S3). OBSERVED_TIMESTAMP is NULL on icecore records,
so the delay applies to the view's observed_at (the event TIMESTAMP). Fewer than 7 points: insufficient history.

Offline tests render the SQL. The sandbox test creates the procedures and views in ICECORE_OBS_<run_id> (the obs_schema
var, so that schema is the run tag). The 40-day series cannot be written by emit_metric (events are stamped now), so
a stand-in view with the METRICS columns holds it; the lag and key tests seed real records with icecore.emit_metric.
The schema is dropped at the end.
"""
import json
import os
import uuid

import pytest

from helpers.events import wait_for_events
from helpers.macros import MacroFailed, call_macro, run_macro

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
REL = '"ICECORE_SANDBOX"."SPIKES"."ORDERS"'


def sql_for(**kwargs) -> str:
    args = {"relation": REL, "metric": "row_count", **kwargs}
    return call_macro("get_metric_history_sql", **args)


# --- offline: rendering only, no connection ------------------------------------------------------

def test_series_has_the_shape_score_expects():
    sql = sql_for()
    assert sql.lstrip().startswith("select observed_at, value")
    assert '."ICECORE_OBS"."METRICS"' in sql and "order by observed_at" in sql


def test_window_is_the_lookback_minus_the_delay_on_snowflakes_utc_clock():
    sql = sql_for(lookback_days=30, delay_minutes=1)
    now = "convert_timezone('UTC', current_timestamp())::timestamp_ntz"
    assert f"observed_at > dateadd(day, -30, {now})" in sql
    assert f"observed_at <= dateadd(minute, -1, {now})" in sql


def test_key_matches_null_column_and_dimension_exactly():
    sql = sql_for()
    assert '"COLUMN" is not distinct from null' in sql and "dimension is not distinct from null" in sql
    sql = sql_for(column="amount", dimension="country=FR")
    assert '"COLUMN" is not distinct from \'amount\'' in sql and "dimension is not distinct from 'country=FR'" in sql
    assert f"relation = '{REL}'" in sql and "metric = 'row_count'" in sql


def test_quotes_in_the_key_are_escaped():
    assert "dimension is not distinct from 'a\\'b'" in sql_for(dimension="a'b")


def test_source_and_schema_arguments():
    assert "from X.Y.Z\n" in sql_for(source="X.Y.Z")
    assert '"MY_OBS"."METRICS"' in sql_for(schema="my_obs")


def test_default_delay_is_one_minute_and_the_var_changes_it():
    assert run_macro("it_history_delay", {}) == 1
    assert run_macro("it_history_delay", {}, ["--vars", json.dumps({"icecore": {"history_delay_minutes": 5}})]) == 5
    assert run_macro("it_history_delay", {"delay_minutes": 0}) == 0  # an argument wins, 0 is a delay too
    assert call_macro("_icecore_history_delay") == 1


@pytest.mark.parametrize("kwargs", [{"lookback_days": 0}, {"lookback_days": "7"}, {"delay_minutes": -1}])
def test_invalid_window_is_refused(kwargs):
    with pytest.raises(MacroFailed):
        sql_for(**kwargs)


# --- sandbox --------------------------------------------------------------------------------------

NOW = "convert_timezone('UTC', current_timestamp())::timestamp_ntz"


def _rel(run_id: str, name: str) -> dict:
    return {"database": DATABASE, "schema": "SPIKES", "identifier": f"it_hist_{name}_{run_id}"}


def _qualified(run_id: str, name: str) -> str:
    return f'"{DATABASE}"."SPIKES"."it_hist_{name}_{run_id}"'


@pytest.fixture(scope="module")
def env(dbt, sf):
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        since = cur.execute(f"select dateadd(minute, -2, {NOW})").fetchone()[0]
        # three real records for one key, written now
        live = _rel(run_id, "live")
        for value in (100, 110, 120):
            run_macro("it_emit_metric", {"rel": {**live, "quoted": True}, "metric": "row_count", "value": value}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=3, since=since, settle_s=5)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        # stand-in for METRICS: 40 daily points (1 h before each whole day back), two keys, one with a column
        qs = f'"{DATABASE}"."{schema}"'
        rel = _qualified(run_id, "long")
        cur.execute(f"""create view {qs}."LONG_SERIES" as
            select '{rel}' as relation, m.metric, m."COLUMN", null::string as dimension, 1000 + seq.n as value,
                   dateadd(hour, -1, dateadd(day, -seq.n, {NOW})) as observed_at
            from (select seq4() as n from table(generator(rowcount => 40))) seq
            cross join (select 'row_count' as metric, null::string as "COLUMN"
                        union all select 'null_rate', 'amount') m""")
        yield {"run_id": run_id, "schema": schema, "obs_vars": obs_vars, "long": f'{qs}."LONG_SERIES"', "long_rel": rel}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def history(env, rel, metric, **kwargs) -> dict:
    return run_macro("it_metric_history", {"rel": rel, "metric": metric, "kwargs": kwargs}, env["obs_vars"])


@pytest.mark.sandbox
def test_40_days_read_with_lookback_30_returns_30_days(sf, env):
    out = history(env, env["long_rel"], "row_count", source=env["long"], lookback_days=30)
    assert out["points"] == 30 and out["insufficient_history"] is False
    days = sf.cursor().execute(
        f"select datediff('day', min(observed_at), max(observed_at)) + 1, min(observed_at) > dateadd(day, -30, {NOW}) "
        f"from ({out['sql']})").fetchone()
    assert days == (30, True)
    assert history(env, env["long_rel"], "row_count", source=env["long"], lookback_days=40)["points"] == 40


@pytest.mark.sandbox
def test_key_isolation_by_metric_and_column(env):
    both = {"source": env["long"]}
    assert history(env, env["long_rel"], "null_rate", column="amount", **both)["points"] == 30
    assert history(env, env["long_rel"], "null_rate", **both)["points"] == 0  # no column: a different series
    assert history(env, env["long_rel"], "volume", **both)["points"] == 0


@pytest.mark.sandbox
def test_fewer_than_7_points_says_insufficient_history(env):
    out = history(env, _qualified(env["run_id"], "live"), "row_count", delay_minutes=0)
    assert out["points"] == 3 and out["insufficient_history"] is True and out["min_points"] == 7
    assert history(env, env["long_rel"], "row_count", source=env["long"], lookback_days=5)["insufficient_history"] is True  # 5 points


@pytest.mark.sandbox
def test_a_record_newer_than_the_history_delay_is_excluded(env):
    live = _qualified(env["run_id"], "live")
    # the delay is explicit: no assertion depends on how long the fixture took (the default is checked offline)
    assert history(env, live, "row_count", delay_minutes=60)["points"] == 0
    assert history(env, live, "row_count", delay_minutes=0)["points"] == 3


@pytest.mark.sandbox
def test_the_series_feeds_score(env):
    out = run_macro("it_metric_history_scored", {"rel": env["long_rel"], "metric": "row_count", "current_value": 1015,
                                                  "kwargs": {"source": env["long"]}}, env["obs_vars"])
    assert len(out["rows"]) == 30 and out["rows"] == sorted(out["rows"])
    scored = out["scored"]
    assert scored["engine"] == "native" and scored["history_points"] == 30 and scored["is_anomaly"] is False
    assert scored["lower"] <= 1015 <= scored["upper"]


@pytest.mark.sandbox
def test_a_failing_read_warns_and_reads_as_insufficient_history(env):
    out = history(env, env["long_rel"], "row_count", source=f'"{DATABASE}"."{env["schema"]}"."NO_SUCH_VIEW"')
    assert out["points"] is None and out["insufficient_history"] is True and out["sql"]
