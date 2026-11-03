"""UC8 compat views `dbt_models`, `dbt_tests` and `dbt_sources` (macros/compat/elementary_nodes.sql).

Offline, the contract column by column: each view's columns, their order and their types equal Elementary's own definition,
parsed from the package pinned in macros/compat/ELEMENTARY_VERSION (helpers/elementary_artifacts.py), and cover every column
spike S4 saw `edr report` read, with S4's types (spikes/results/s4.json). The views select only from the core views
NODE_EXECUTIONS, TEST_RESULTS and LINEAGE_LATEST. Sandbox: a real `dbt build` with the hooks, the core views and the compat
views created in run_id-named schemas, DESCRIBE types equal Elementary's, row counts equal the core views' distinct nodes.
"""
import json
import re
import time

import pytest

from helpers.elementary_artifacts import artifact_columns
from helpers.elementary_package import SNOWFLAKE_TYPE, elementary_package  # noqa: F401
from helpers.macros import IT_DIR, run_macro
from helpers.run_hooks import (
    DATABASE, drop_fixture_views, icecore_vars, invocation_records, node_results, obs_schema, run_with_hooks,
    snowflake_since,
)

S4 = json.load(open(IT_DIR.parent / "spikes" / "results" / "s4.json"))
RELATIONS = {"dbt_models": 23, "dbt_tests": 29, "dbt_sources": 22}
CORE = {"NODE_EXECUTIONS", "TEST_RESULTS", "LINEAGE_LATEST", "LINEAGE", "INVOCATIONS"}


def rendered() -> dict:
    return run_macro("it_compat_nodes", {})


def pairs(columns):
    return [(c["name"], c["type"]) for c in columns]


# --- offline ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("relation", RELATIONS)
def test_columns_equal_elementarys_definition_in_name_order_and_type(elementary_package, relation):
    expected = artifact_columns(relation)
    assert len(expected) == RELATIONS[relation]
    assert pairs(rendered()[relation.replace("dbt_", "")]) == expected


@pytest.mark.parametrize("relation", RELATIONS)
def test_every_column_s4_saw_edr_read_is_there_with_s4s_type(elementary_package, relation):
    mine = {c["name"]: SNOWFLAKE_TYPE[c["type"]] for c in rendered()[relation.replace("dbt_", "")]}
    for column, snowflake_type in S4["types"][relation].items():
        assert mine[column] == snowflake_type, column


@pytest.mark.parametrize("relation", RELATIONS)
def test_each_view_selects_only_from_core_views_and_casts_every_column(relation):
    key = relation.replace("dbt_", "")
    out = rendered()
    sql = out[key + "_sql"]
    assert "base_events" not in sql.lower()
    used = set(re.findall(r'"DB"\."OBS"\."(\w+)"', sql))
    assert used and used <= CORE, used
    assert sql.count("cast(") == len(out[key])
    for column in out[key]:
        assert f" as {column['name']}" in sql


def test_models_fill_identity_materialization_relation_parts_and_parents_and_null_the_rest():
    columns = {c["name"]: c["expr"] for c in rendered()["models"]}
    assert columns["unique_id"] == "t.unique_id" and columns["name"] == "t.name"
    assert columns["materialization"] == "t.materialization"
    assert "split_part(t.relation, '.', 3)" in columns["alias"]
    assert "split_part(t.relation, '.', 1)" in columns["database_name"] and "split_part(t.relation, '.', 2)" in columns["schema_name"]
    assert columns["depends_on_nodes"] == "t.parents" and '"DB"."OBS"."LINEAGE_LATEST"' in rendered()["models_sql"]
    assert sorted(n for n, e in columns.items() if e is None) == sorted([
        "checksum", "tags", "meta", "owner", "depends_on_macros", "description", "original_path", "path", "patch_path",
        "metadata_hash", "unique_key", "incremental_strategy", "group_name", "access"])


def test_tests_fill_name_short_name_column_severity_and_type_and_null_the_rest():
    columns = {c["name"]: c["expr"] for c in rendered()["tests"]}
    assert (columns["name"], columns["short_name"], columns["test_column_name"], columns["severity"], columns["type"]) == (
        "t.name", "t.test_name", "t.test_column", "t.severity", "t.test_type")
    assert columns["parent_model_unique_id"] == "t.tested_unique_id" and columns["depends_on_nodes"] is None
    assert len([n for n, e in columns.items() if e is None]) == 29 - 9


def test_sources_are_read_from_the_unique_id_and_lineage():
    out = rendered()
    columns = {c["name"]: c["expr"] for c in out["sources"]}
    assert "split_part(t.unique_id, '.', 3)" in columns["source_name"] and "split_part(t.unique_id, '.', 4)" in columns["name"]
    assert "parent_unique_id like 'source.%'" in out["sources_sql"]
    assert len([n for n, e in columns.items() if e is None]) == 22 - 5


def test_the_three_views_are_in_the_compat_list_after_the_run_views():
    names = [v["name"] for v in run_macro("it_compat_views_render", {})]
    assert names.index("DBT_RUN_RESULTS") < names.index("DBT_MODELS") and {"DBT_TESTS", "DBT_SOURCES"} <= set(names)


# --- sandbox --------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_a_build_through_the_node_compat_views(sf, run_id, elementary_package):
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
            run_macro("it_create_compat_views", {"schema": obs}, obs_vars)
            try:
                cur = sf.cursor()
                for relation in RELATIONS:
                    columns = cur.execute(
                        f"select column_name, data_type from {DATABASE}.information_schema.columns where table_schema = '{compat}' "
                        f"and table_name = '{relation.upper()}' order by ordinal_position").fetchall()
                    assert columns == [(n.upper(), SNOWFLAKE_TYPE[t]) for n, t in artifact_columns(relation)], relation
                q = lambda sql: cur.execute(sql).fetchall()  # noqa: E731
                ne = f'"{DATABASE}"."{obs}"."NODE_EXECUTIONS"'
                deadline = time.monotonic() + 180
                while True:  # the core views read the event table, which fills asynchronously
                    core = {t: q(f"select count(distinct unique_id) from {ne} where type = '{t}' and invocation_id = '{inv}'")[0][0]
                            for t in ("model", "test")}
                    expected = {t: sum(1 for r in node_results(results) if r["unique_id"].startswith(t + ".")) for t in core}
                    if core == expected or time.monotonic() > deadline:
                        break
                    time.sleep(5)
                assert core == expected and core["model"] > 0
                count = lambda rel, where="": q(f'select count(*) from "{DATABASE}"."{compat}"."{rel}" {where}')[0][0]  # noqa: E731
                # the compat views list every node that ever ran in this schema: here, this build's
                assert count("DBT_MODELS") == core["model"]
                assert count("DBT_TESTS") == core["test"]
                models = cur.execute(f'select * from "{DATABASE}"."{compat}"."DBT_MODELS"')
                names = [d[0].lower() for d in models.description]
                rows = [dict(zip(names, r)) for r in models.fetchall()]
                assert all(r["unique_id"].startswith("model.") and r["name"] and r["materialization"] and r["alias"] and
                           r["database_name"] and r["schema_name"] and r["package_name"] and r["generated_at"] for r in rows), rows
                assert q(f'select count(*) from "{DATABASE}"."{compat}"."DBT_MODELS" where '
                         f'datediff(day, cast(generated_at as timestamp), current_timestamp()) < 7')[0][0] == len(rows)
                assert q(f'select count(*) from "{DATABASE}"."{compat}"."DBT_TESTS" where unique_id not like \'test.%\'')[0][0] == 0
                # lineage: any parent of a model shows in depends_on_nodes as a JSON list
                if q(f'select count(*) from "{DATABASE}"."{obs}"."LINEAGE_LATEST"')[0][0]:
                    parents = [r[0] for r in q(f'select depends_on_nodes from "{DATABASE}"."{compat}"."DBT_MODELS" where depends_on_nodes is not null')]
                    assert all(isinstance(json.loads(p), list) for p in parents)
            finally:
                sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{compat}" cascade')
    finally:
        drop_fixture_views(sf, run_id)
