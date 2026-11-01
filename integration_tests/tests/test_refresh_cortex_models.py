"""UC3 Cortex model refresh: the task that retrains each monitored table's model from the metric history
(macros/control/refresh_cortex_models.sql), created by setup's create_tasks step.

Security (lead ruling): the task reads `relation` from records the dbt role can write and runs with setup's rights, so no SQL
is built from a record value. Offline tests check the rendering, the hash-derived names, the strict relation shape, and the
skip when Cortex is off. The sandbox test uses a schema ICECORE_OBS_<run_id> of its own and a fixture metrics table in it with
one monitored table (hostile name: quote, backslash, $$, semicolon, newline) and one invalid relation, creates the task
suspended, runs its body (EXECUTE TASK when the role may, else the same body directly, which is what QUERY_HISTORY then
shows), checks the one model and that no extra statement ran, scores with it through ICECORE_SCORE, and drops the schema.
"""
import hashlib
import json
import re
import uuid
from datetime import timedelta

import pytest

from helpers.macros import MacroFailed, call_macro, run_macro_with_logs

DATABASE = "ICECORE_SANDBOX"
RELATION = '"ICECORE_SANDBOX"."SPIKES"."ORDERS"'
SENTINEL = "SENTINEL_INJECTED"
INJECTED = f"INJECTED_{uuid.uuid4().hex.upper()}"  # an injection would create this table
HOSTILE = ('"ICECORE_SANDBOX"."SPIKES"."ORD\\\' ); create table ICECORE_SANDBOX.SPIKES.' + INJECTED
           + ' (a int); select \'' + SENTINEL + '\' $$ ; -- \nX"')  # one valid-shaped name with \' ) ; $$ and a newline
INVALID = "x'; select '" + SENTINEL + "' --"  # not the quoted-identifier shape
CORTEX_OFF = {"cortex": False, "cortex_reason": "SNOWFLAKE.ML.ANOMALY_DETECTION is not available in this account or region"}
CORTEX_ON = {"cortex": True, "cortex_reason": None}


def call(name, kwargs=None, extra=None):
    return run_macro_with_logs("it_call", {"name": name, "kwargs": kwargs or {}}, extra)


# --- names and keys (offline) -----------------------------------------------------------------------


def test_model_name_is_the_hash_of_the_relation_not_its_text():
    name, _ = call("cortex_model_name", {"relation": HOSTILE, "schema": "icecore_obs_x"})
    digest = hashlib.md5(HOSTILE.encode()).hexdigest()[:16].upper()
    assert name.endswith(f".ICECORE_OBS_X.ICECORE_M_{digest}"), name
    assert re.fullmatch(r"[A-Z0-9_$]+\.ICECORE_OBS_X\.ICECORE_M_[0-9A-F]{16}", name), name  # plain, no relation text
    other, _ = call("cortex_model_name", {"relation": RELATION, "schema": "icecore_obs_x"})
    assert other != name


def test_series_key_is_metric_column_dimension_with_tilde_for_missing():
    assert call("cortex_series_key", {"metric": "row_count"})[0] == "row_count|~|~"
    assert call("cortex_series_key", {"metric": "null_rate", "column": "amount", "dimension": "country=FR"})[0] == \
        "null_rate|amount|country=FR"


# --- the task body (offline) ------------------------------------------------------------------------


def body(**kw):
    return call("render_refresh_cortex_models_sql", {"schema": "icecore_obs_x", **kw})[0]


def literals_removed(sql):
    return re.sub(r"'(?:\\.|''|[^'\\])*'", "''", sql, flags=re.S)


def test_body_reads_the_metrics_view_in_the_history_window_and_creates_one_model_per_table():
    sql = body()
    assert '"ICECORE_OBS_X"."METRICS"' in sql
    assert "dateadd(day, -30, sysdate())" in sql and "dateadd(minute, -1, sysdate())" in sql  # lookback 30, delay 1 (history_delay_minutes)
    assert "n >= 14" in sql  # the engine's 14-point rule per series
    assert sql.count("CREATE OR REPLACE SNOWFLAKE.ML.ANOMALY_DETECTION") == 1  # inside the per-table loop
    assert "FOR r IN rels DO" in sql and "EXCEPTION" in sql
    assert "INSERT" not in sql.upper() and "$$" not in sql


def test_body_builds_no_sql_from_a_record_value():
    sql = body()
    # the relation is validated, hashed and bound; it is never concatenated into a statement or escaped with a local REPLACE
    assert "REPLACE(" not in sql
    assert "REGEXP_LIKE(r.relation, '\"[^\"]+\"\\\\.\"[^\"]+\"\\\\.\"[^\"]+\"')" in sql
    assert "md5(relation) = ' || qq || digest || qq" in sql  # a hex digest filters the rows, not the relation text
    assert "'ICECORE_M_' || UPPER(LEFT(digest, 16))" in sql  # the model name comes from the hash
    assert "'dbt.node.relation', r.relation" in sql  # the mapping event takes it as a value
    # every statement text is built from the macro's constants, `digest` and `model_id` only: no other variable is concatenated
    concatenated = set(re.findall(r"\|\| ([a-z_.]+) \|\|", literals_removed(sql).replace("''", "'")))
    assert concatenated <= {"q", "qq", "digest", "model_id", "train", "skipped", "SQLCODE"}, concatenated
    assert "r.relation" not in "".join(re.findall(r"EXECUTE IMMEDIATE[^;]*;", sql))


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
        call("create_emit_procedures", {"schema": schema})
        cur.execute(f'create table {fixture} (relation varchar, "COLUMN" varchar, dimension varchar, metric varchar, observed_at timestamp_ntz, value float)')
        base = cur.execute("select date_trunc('hour', sysdate())").fetchone()[0]  # one base: every point on the same daily grid
        for i in range(20):
            for rel, col, metric, value in ((HOSTILE, None, "row_count", 1000 + (i % 5) * 3), (HOSTILE, "amount", "null_rate", 0.02 + (i % 4) * 0.001),
                                            (INVALID, None, "row_count", 5 + (i % 3))):
                cur.execute(f"insert into {fixture} select %s, %s, null, %s, dateadd(day, {i - 21}, %s::timestamp_ntz), %s", (rel, col, metric, base, value))
        created, _ = call("create_refresh_cortex_task", {"schema": schema, "capabilities": CORTEX_ON, "source": fixture})
        info = {"created": created, "schema": schema, "next": str(base + timedelta(days=-1))}
        if created["status"] == "ok":
            info["state"] = cur.execute(f"show tasks like 'ICECORE_REFRESH_CORTEX' in schema \"{DATABASE}\".\"{schema}\"").fetchall()
            try:
                cur.execute(f'execute task "{DATABASE}"."{schema}"."ICECORE_REFRESH_CORTEX"')  # never resumed
                info["ran"] = "execute task"
            except Exception as exc:
                info["ran"] = f"execute task refused: {exc}"
            if info["ran"] == "execute task":
                import time
                for _ in range(60):
                    if cur.execute(f'show snowflake.ml.anomaly_detection in schema "{DATABASE}"."{schema}"').fetchall():
                        break
                    time.sleep(5)
            # the same body, run by this session so QUERY_HISTORY_BY_SESSION shows every statement it issued
            sql, _ = call("render_refresh_cortex_models_sql", {"schema": schema, "source": fixture})
            started = cur.execute("select current_timestamp()").fetchone()[0]
            info["body_result"] = cur.execute("execute immediate $$\n" + sql + "\n$$").fetchone()[0]
            info["statements"] = [r[0] for r in cur.execute(
                "select query_text from table(information_schema.query_history(end_time_range_start => dateadd(minute, -15, current_timestamp()), result_limit => 10000))"
                " where start_time >= %s and session_id = current_session() order by start_time", (started,)).fetchall()]
        info["injected"] = cur.execute(f"show tables like '{INJECTED}' in schema {DATABASE}.SPIKES").fetchall()
        for _ in info["injected"]:
            cur.execute(f"drop table if exists {DATABASE}.SPIKES.{INJECTED}")
        info["models"] = [r[1] for r in cur.execute(f'show snowflake.ml.anomaly_detection in schema "{DATABASE}"."{schema}"').fetchall()]
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
def test_running_the_task_leaves_one_model_named_by_the_hash(refreshed):
    name, _ = call("cortex_model_name", {"relation": HOSTILE, "schema": refreshed["schema"]})
    assert refreshed["models"] == [name.split(".")[-1]], (refreshed["models"], refreshed.get("ran"), refreshed.get("body_result"))
    assert refreshed["body_result"].startswith("ok 1 models, 1 skipped"), refreshed["body_result"]  # the invalid relation was skipped


@pytest.mark.sandbox
def test_no_extra_statement_ran_for_a_hostile_relation(refreshed):
    """INFORMATION_SCHEMA.QUERY_HISTORY lists a Scripting block's top-level statements only, not the ones it runs, so the proof is
    behavioural: the relation (a quote, a backslash, `)`, `;`, `$$`, a newline, and a CREATE TABLE of its own) built no table,
    the invalid relation was skipped, and exactly one model came out. The rendering tests show why: nothing is concatenated."""
    assert refreshed["injected"] == [], "the relation's own CREATE TABLE ran"
    top = [t for t in refreshed["statements"] if not t.lstrip().lower().startswith("execute immediate $$")]
    assert not [t for t in top if SENTINEL in t]
    assert len(refreshed["models"]) == 1 and refreshed["body_result"].startswith("ok 1 models, 1 skipped")


@pytest.mark.sandbox
def test_the_model_scores_both_series_through_the_score_procedure(refreshed):
    name, _ = call("cortex_model_name", {"relation": HOSTILE, "schema": refreshed["schema"]})
    for metric, column, value in (("row_count", None, 100000), ("null_rate", "amount", 0.9)):
        out, _ = call("cortex__score", {
            "model": name, "series": call_macro("cortex_series_key", metric=metric, column=column),
            "series_sql": "select sysdate() as observed_at, 1::float as value where false", "current_value": value,
            "sensitivity": "medium", "min_consecutive": 1, "observed_at": refreshed["next"], "schema": refreshed["schema"]})
        assert out.get("engine") == "cortex" and out["is_anomaly"] is True, out


@pytest.mark.sandbox
def test_the_mapping_is_recorded_and_the_dbt_role_needs_only_the_procedure_grant(refreshed):
    printed = refreshed["created"]["printed"]
    assert len(printed) == 1 and printed[0].startswith("GRANT USAGE ON PROCEDURE ") and '"ICECORE_SCORE"(STRING, STRING, STRING, STRING, ARRAY, FLOAT, FLOAT)' in printed[0], printed
    assert not [p for p in printed if "ANOMALY_DETECTION" in p]  # no grant on a model: models cannot be shared across roles
