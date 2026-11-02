"""UC8 compat views `dbt_invocations` and `dbt_run_results` (macros/compat/elementary_runs.sql).

Offline, the contract column by column: each view's columns, their order and their types equal Elementary's own definition,
parsed from the package pinned in macros/compat/ELEMENTARY_VERSION (helpers/elementary_artifacts.py), and cover every
column spike S4 saw `edr report` read, with S4's types (spikes/results/s4.json). The views select only from the core views
INVOCATIONS and NODE_EXECUTIONS, fill project_name and elementary_version with real values, and type every other
unfillable column as NULL. Sandbox: a real `dbt build` with the hooks, the core views and the compat views created in
run_id-named schemas, DESCRIBE types equal Elementary's, row counts equal the core views', edr's filter casts work.
"""
import json
import time

import pytest

from helpers.elementary_artifacts import artifact_columns
from helpers.elementary_package import SNOWFLAKE_TYPE, elementary_package, pinned_version  # noqa: F401
from helpers.macros import IT_DIR, run_macro
from helpers.run_hooks import (
    DATABASE, drop_fixture_views, icecore_vars, invocation_records, node_results, obs_schema, run_with_hooks,
    snowflake_since,
)

S4 = json.load(open(IT_DIR.parent / "spikes" / "results" / "s4.json"))
RELATIONS = {"dbt_invocations": ("invocations", "invocations_sql", 35, '."INVOCATIONS"'),
             "dbt_run_results": ("run_results", "run_results_sql", 23, '."NODE_EXECUTIONS"')}


def rendered() -> dict:
    return run_macro("it_compat_runs", {})


def pairs(columns):
    return [(c["name"], c["type"]) for c in columns]


# --- offline ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("relation", RELATIONS)
def test_columns_equal_elementarys_definition_in_name_order_and_type(elementary_package, relation):
    key, _, count, _ = RELATIONS[relation]
    expected = artifact_columns(relation)
    assert len(expected) == count
    assert pairs(rendered()[key]) == expected


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_column_s4_saw_edr_read_is_there_with_s4s_type(elementary_package, relation):
    mine = {c["name"]: SNOWFLAKE_TYPE[c["type"]] for c in rendered()[RELATIONS[relation][0]]}
    for column, snowflake_type in S4["types"][relation].items():
        assert mine[column] == snowflake_type, column


def test_the_package_version_is_the_pinned_one_and_the_view_claims_it(elementary_package):
    assert pinned_version() == "0.26.1" == rendered()["version"]
    exprs = {c["name"]: c["expr"] for c in rendered()["invocations"]}
    assert exprs["elementary_version"] == "'0.26.1'"


@pytest.mark.parametrize("relation", RELATIONS)
def test_each_view_selects_only_from_its_core_view_and_casts_every_column(relation):
    key, sql_key, _, core = RELATIONS[relation]
    out = rendered()
    sql = out[sql_key]
    assert sql.endswith(f'from "DB"."OBS"{core} as t')
    assert "base_events" not in sql.lower()
    assert sql.count("cast(") == len(out[key])
    for column in out[key]:
        assert f" as {column['name']}" in sql


def test_dbt_invocations_fills_project_name_and_the_run_times_and_nulls_the_rest():
    columns = {c["name"]: c["expr"] for c in rendered()["invocations"]}
    assert columns["project_name"] == "t.project" and columns["invocation_id"] == "t.invocation_id"
    assert columns["command"] == "t.command" and columns["dbt_version"] == "t.dbt_version"
    assert columns["target_name"] == "t.target"
    assert "t.started_at" in columns["run_started_at"] and "t.ended_at" in columns["run_completed_at"]
    assert sorted(n for n, e in columns.items() if e is None) == sorted([
        "job_id", "job_name", "job_run_id", "full_refresh", "invocation_vars", "vars", "target_database",
        "target_schema", "target_profile_name", "threads", "selected", "yaml_selector", "project_id", "env", "env_id",
        "cause_category", "cause", "pull_request_id", "git_sha", "orchestrator", "dbt_user", "job_url", "job_run_url",
        "account_id", "target_adapter_specific_fields"])


def test_dbt_run_results_fills_ids_times_and_query_id_and_nulls_the_rest():
    columns = {c["name"]: c["expr"] for c in rendered()["run_results"]}
    assert columns["model_execution_id"] == "t.invocation_id || '.' || t.unique_id"
    assert columns["execution_time"] == "t.execution_time_s" and columns["query_id"] == "t.query_id"
    assert columns["resource_type"] == "t.type" and columns["created_at"] == "t.recorded_at"
    assert "t.started_at" in columns["execute_started_at"] and "t.completed_at" in columns["execute_completed_at"]
    assert sorted(n for n, e in columns.items() if e is None) == sorted([
        "message", "compile_started_at", "compile_completed_at", "full_refresh", "compiled_code", "failures",
        "thread_id", "adapter_response", "group_name"])


def test_the_two_views_are_in_the_compat_list_after_the_test_results_view():
    names = [v["name"] for v in run_macro("it_compat_views_render", {})]
    assert names[:1] == ["ELEMENTARY_TEST_RESULTS"] and {"DBT_INVOCATIONS", "DBT_RUN_RESULTS"} <= set(names)


def test_without_compat_these_views_are_not_created():
    from helpers.macros import call_macro
    assert "create_compat_views" not in [s["name"] for s in call_macro("_icecore_setup_steps", compat="none")]


# --- sandbox --------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_a_build_through_the_compat_views(sf, run_id, elementary_package):
    try:
        with obs_schema(sf) as obs:
            compat = f"{obs}_ELEMENTARY"
            obs_vars = ["--vars", json.dumps(icecore_vars(obs))]
            since = snowflake_since(sf)
            res, results = run_with_hooks(["build", "--select", "hooks_a+"], run_id, obs)
            assert res.success, res.exception
            inv = results["metadata"]["invocation_id"]
            invocation_records(sf, obs, inv, min_rows=3 + 2 * len(node_results(results)), since=since)
            run_macro("it_create_views", {"schema": obs}, obs_vars)
            created = run_macro("it_create_compat_views", {"schema": obs}, obs_vars)
            assert f'"{DATABASE}"."{compat}"."DBT_INVOCATIONS"' in created and f'"{DATABASE}"."{compat}"."DBT_RUN_RESULTS"' in created
            try:
                cur = sf.cursor()
                for relation in RELATIONS:
                    columns = cur.execute(
                        f"select column_name, data_type from {DATABASE}.information_schema.columns where table_schema = '{compat}' "
                        f"and table_name = '{relation.upper()}' order by ordinal_position").fetchall()
                    assert columns == [(n.upper(), SNOWFLAKE_TYPE[t]) for n, t in artifact_columns(relation)], relation
                q = lambda sql, *a: cur.execute(sql, a).fetchall()  # noqa: E731
                deadline = time.monotonic() + 120
                while True:  # the core view reads the event table, which fills asynchronously
                    core_runs = q(f'select count(*) from "{DATABASE}"."{obs}"."NODE_EXECUTIONS" where invocation_id = %s', inv)[0][0]
                    if core_runs == len(node_results(results)) or time.monotonic() > deadline:
                        break
                    time.sleep(5)
                assert core_runs == len(node_results(results))
                assert q(f'select count(*) from "{DATABASE}"."{compat}"."DBT_RUN_RESULTS" where invocation_id = %s', inv)[0][0] == core_runs
                assert q(f'select count(*) from "{DATABASE}"."{compat}"."DBT_INVOCATIONS" where invocation_id = %s', inv)[0][0] == 1
                totals = q(f'select (select count(*) from "{DATABASE}"."{compat}"."DBT_RUN_RESULTS"), '
                           f'(select count(*) from "{DATABASE}"."{obs}"."NODE_EXECUTIONS"), '
                           f'(select count(*) from "{DATABASE}"."{compat}"."DBT_INVOCATIONS"), '
                           f'(select count(*) from "{DATABASE}"."{obs}"."INVOCATIONS")')[0]
                assert totals[0] == totals[1] and totals[2] == totals[3]  # row counts match the core views

                one = cur.execute(f'select * from "{DATABASE}"."{compat}"."DBT_INVOCATIONS" where invocation_id = %s', (inv,))
                row = dict(zip([d[0].lower() for d in one.description], one.fetchone()))
                assert row["project_name"] and row["elementary_version"] == "0.26.1"
                assert row["command"] == "build" and row["dbt_version"] and row["target_name"]
                assert row["run_started_at"] and row["run_completed_at"] and row["created_at"] is not None
                # edr's own filter on the text timestamps: they cast
                assert q(f'select count(*) from "{DATABASE}"."{compat}"."DBT_RUN_RESULTS" where invocation_id = %s '
                         f'and datediff(day, cast(execute_completed_at as timestamp), current_timestamp()) < 7', inv)[0][0] == core_runs
                runs = cur.execute(f'select * from "{DATABASE}"."{compat}"."DBT_RUN_RESULTS" where invocation_id = %s', (inv,))
                names = [d[0].lower() for d in runs.description]
                models = [dict(zip(names, r)) for r in runs.fetchall() if r[names.index("resource_type")] == "model"]
                assert models and all(m["model_execution_id"] == f"{inv}.{m['unique_id']}" and m["query_id"] and
                                      m["status"] == "success" and m["execution_time"] is not None for m in models)
            finally:
                sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{compat}" cascade')
    finally:
        drop_fixture_views(sf, run_id)
