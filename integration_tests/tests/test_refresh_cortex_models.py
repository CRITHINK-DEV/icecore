"""UC3 Cortex model refresh: the task that retrains each monitored table's model from the metric history
(macros/control/refresh_cortex_models.sql), created by setup's create_tasks step.

Offline tests check the rendering, the names and the skip when Cortex is off. The sandbox test uses a schema
ICECORE_OBS_<run_id> of its own and a fixture metrics table in it (one monitored table with two series), creates the
task suspended, runs its body once with EXECUTE TASK (the task is never resumed), checks the one model it created,
scores with it, and drops the schema (and so the task and the model) at the end.
"""
import json
import re
import uuid
from datetime import timedelta

import pytest

from helpers.macros import MacroFailed, call_macro, run_macro_with_logs
from test_engine_contract import daily_series_sql  # noqa: F401

DATABASE = "ICECORE_SANDBOX"
RELATION = '"ICECORE_SANDBOX"."SPIKES"."ORDERS"'
CORTEX_OFF = {"cortex": False, "cortex_reason": "SNOWFLAKE.ML.ANOMALY_DETECTION is not available in this account or region"}
CORTEX_ON = {"cortex": True, "cortex_reason": None}


def call(name, kwargs=None, extra=None):
    return run_macro_with_logs("it_call", {"name": name, "kwargs": kwargs or {}}, extra)


# --- names and keys (offline) -----------------------------------------------------------------------


def test_model_name_is_in_the_obs_schema_sanitized_and_stable():
    name, _ = call("cortex_model_name", {"relation": RELATION, "schema": "icecore_obs_x"})
    assert re.fullmatch(r'"[^"]+"\."ICECORE_OBS_X"\."ICECORE_CX__ICECORE_SANDBOX___SPIKES___ORDERS__[0-9A-F]{8}"', name), name
    assert call("cortex_model_name", {"relation": RELATION, "schema": "icecore_obs_x"})[0] == name
    other, _ = call("cortex_model_name", {"relation": '"ICECORE_SANDBOX"."SPIKES"."ORDERS2"', "schema": "icecore_obs_x"})
    assert other != name


def test_series_key_is_the_compact_json_array_with_nulls():
    assert call("cortex_series_key", {"metric": "row_count"})[0] == '["row_count",null,null]'
    assert call("cortex_series_key", {"metric": "null_rate", "column": "amount", "dimension": "country=FR"})[0] == \
        '["null_rate","amount","country=FR"]'


# --- the task body (offline) ------------------------------------------------------------------------


def body(**kw):
    return call("render_refresh_cortex_models_sql", {"schema": "icecore_obs_x", **kw})[0]


def test_body_reads_the_metrics_view_in_the_history_window_and_creates_one_model_per_table():
    sql = body()
    assert 'FROM "' in sql and '"ICECORE_OBS_X"."METRICS"' in sql
    assert "dateadd(day, -30, sysdate())" in sql and "dateadd(minute, -1, sysdate())" in sql  # lookback 30, delay 1 (history_delay_minutes)
    assert "n >= 14" in sql  # the engine's 14-point rule per series
    assert sql.count("CREATE OR REPLACE SNOWFLAKE.ML.ANOMALY_DETECTION") == 1  # inside the per-table loop
    assert "FOR r IN rels DO" in sql and "EXCEPTION" in sql
    assert "INSERT" not in sql.upper() and "$$" not in sql


def test_body_follows_the_history_delay_var_and_validates_its_arguments():
    out, _ = call("render_refresh_cortex_models_sql", {"schema": "x"}, ["--vars", json.dumps({"icecore": {"history_delay_minutes": 5}})])
    assert "dateadd(minute, -5, sysdate())" in out
    with pytest.raises(MacroFailed, match="min_points"):
        body(min_points=5)
    with pytest.raises(MacroFailed, match="lookback_days"):
        body(lookback_days=0)


# --- the task (offline) -----------------------------------------------------------------------------


def test_cortex_off_creates_no_task_and_runs_no_query():
    out, _ = call("create_refresh_cortex_task", {"schema": "icecore_obs_x", "capabilities": CORTEX_OFF})
    assert out["status"] == "skipped" and out["task"] is None  # a query would have failed to connect


def test_create_tasks_is_one_list_with_the_refresh_entry():
    entries, _ = call("_icecore_task_entries")
    assert [e["name"] for e in entries] == ["cortex_refresh"]
    out, _ = call("create_tasks", {"schema": "icecore_obs_x", "capabilities": CORTEX_OFF})
    assert [(t["name"], t["status"]) for t in out["tasks"]] == [("cortex_refresh", "skipped")]


def test_the_task_is_created_suspended_on_a_schedule_from_cortex_refresh_days():
    ddl, _ = call("_icecore_refresh_task_ddl", {"task": "T", "body": "BEGIN RETURN 1; END;", "warehouse": "WH", "schedule_minutes": 7 * 1440})
    assert ddl.startswith("CREATE OR REPLACE TASK T") and "SCHEDULE = '10080 MINUTE'" in ddl
    assert "RESUME" not in ddl  # suspended: a new task is, and nothing resumes it here


@pytest.mark.parametrize("days", ["7", 0, 1.5])
def test_cortex_refresh_days_must_be_whole_days(days):
    with pytest.raises(MacroFailed, match="cortex_refresh_days"):
        call_macro("create_refresh_cortex_task", schema="x", capabilities=CORTEX_ON, days=days)


# --- sandbox ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def refreshed(sf):
    caps, _ = call("detect_capabilities", {"record": False})
    if not caps["cortex"]:
        pytest.skip(f"Cortex anomaly detection is not available to this role: {caps['cortex_reason']}")
    run_id = uuid.uuid4().hex.upper()
    schema = f"ICECORE_OBS_{run_id}"
    fixture = f'"{DATABASE}"."{schema}"."IT_METRICS"'
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        cur.execute(f'create table {fixture} (relation varchar, "COLUMN" varchar, dimension varchar, metric varchar, observed_at timestamp_ntz, value float)')
        rows = []
        for i in range(20):
            rows.append((RELATION, None, None, "row_count", i, 1000 + (i % 5) * 3))
            rows.append((RELATION, "amount", None, "null_rate", i, 0.02 + (i % 4) * 0.001))
        base = cur.execute("select date_trunc('hour', sysdate())").fetchone()[0]  # one base: every point on the same daily grid
        for rel, col, dim, metric, i, value in rows:
            cur.execute(f"insert into {fixture} select %s, %s, %s, %s, dateadd(day, {i - 21}, %s::timestamp_ntz), %s", (rel, col, dim, metric, base, value))
        info_base = base
        created, _ = call("create_refresh_cortex_task", {"schema": schema, "capabilities": CORTEX_ON, "source": fixture})
        info = {"created": created, "schema": schema, "fixture": fixture, "next": str(info_base + timedelta(days=-1))}
        if created["status"] == "ok":
            task = f'"{DATABASE}"."{schema}"."ICECORE_REFRESH_CORTEX"'
            info["state"] = cur.execute(f"show tasks like 'ICECORE_REFRESH_CORTEX' in schema \"{DATABASE}\".\"{schema}\"").fetchall()
            try:
                cur.execute(f"execute task {task}")  # runs the body once; the task is never resumed
                info["ran"] = "execute task"
            except Exception as exc:
                info["ran"] = f"execute task refused: {exc}"
            if info["ran"] == "execute task":
                import time
                for _ in range(60):
                    n = cur.execute(f"show snowflake.ml.anomaly_detection in schema \"{DATABASE}\".\"{schema}\"").fetchall()
                    if n:
                        break
                    time.sleep(5)
            else:  # the role lacks EXECUTE TASK: run the same body directly, and say so in the PR
                body, _ = call("render_refresh_cortex_models_sql", {"schema": schema, "source": fixture})
                info["body_result"] = cur.execute("execute immediate $$\n" + body + "\n$$").fetchone()[0]
        info["models"] = [r[1] for r in cur.execute(f"show snowflake.ml.anomaly_detection in schema \"{DATABASE}\".\"{schema}\"").fetchall()]
        yield info
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


@pytest.mark.sandbox
def test_the_task_exists_and_is_suspended(refreshed):
    assert refreshed["created"]["status"] == "ok", refreshed["created"]
    assert refreshed["created"]["resumed"] is False
    (row,) = refreshed["state"]
    assert "suspended" in [str(c).lower() for c in row], row


@pytest.mark.sandbox
def test_running_the_task_leaves_one_model_for_the_table(refreshed):
    name, _ = call("cortex_model_name", {"relation": RELATION, "schema": refreshed["schema"]})
    assert refreshed["models"] == [name.split(".")[-1].strip('"')], (refreshed["models"], refreshed.get("ran"), refreshed.get("body_result"))


@pytest.mark.sandbox
def test_the_model_scores_both_series_through_the_engine(refreshed):
    name, _ = call("cortex_model_name", {"relation": RELATION, "schema": refreshed["schema"]})
    for metric, column, value in (("row_count", None, 100000), ("null_rate", "amount", 0.9)):
        out = call_macro("cortex__score", model=name, series=call_macro("cortex_series_key", metric=metric, column=column),
                         series_sql="select sysdate() as observed_at, 1::float as value where false", current_value=value,
                         sensitivity="medium", min_consecutive=1, observed_at=refreshed["next"])
        assert out.get("engine") == "cortex" and out["is_anomaly"] is True, out


@pytest.mark.sandbox
def test_setup_lists_the_task_when_cortex_is_available_and_prints_the_grant(refreshed):
    printed = refreshed["created"]["printed"]
    assert any(p.startswith("GRANT USAGE ON ALL SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA") for p in printed), printed
    assert any(p.startswith("GRANT USAGE ON FUTURE SNOWFLAKE.ML.ANOMALY_DETECTION IN SCHEMA") for p in printed), printed
