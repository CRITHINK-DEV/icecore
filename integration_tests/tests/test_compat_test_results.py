"""UC8 compat view `elementary_test_results` (macros/compat/elementary_tests.sql) and the setup step that creates it
(macros/compat/create_compat_views.sql, run when setup gets `compat: elementary`).

Offline, the contract: the view's columns, their order and their types equal Elementary's own definition, parsed from the
package pinned in macros/compat/ELEMENTARY_VERSION (empty_elementary_test_results in its empty_table.sql), and cover
every column spike S4 saw `edr report` read, with S4's types (spikes/results/s4.json). The view selects only from the
core view TEST_RESULTS. Sandbox: the views are created in a compat schema and selected from, the described types equal
Elementary's, the row count equals the core view's, and without compat nothing is created.
"""
import json
import time
import uuid

import pytest

from helpers.elementary_package import SNOWFLAKE_TYPE, elementary_package, pinned_version, table_columns  # noqa: F401
from helpers.macros import IT_DIR, call_macro, run_macro
from helpers.run_hooks import DATABASE, obs_schema, snowflake_since

S4 = json.load(open(IT_DIR.parent / "spikes" / "results" / "s4.json"))


def view_columns(sql_columns):
    return [(c["name"], c["type"]) for c in sql_columns]


# --- offline ---------------------------------------------------------------------------------------

def test_columns_equal_elementarys_definition_in_name_order_and_type(elementary_package):
    mine = run_macro("it_compat_test_results", {})["columns"]
    expected = table_columns("elementary_test_results")
    assert len(expected) == 28
    assert view_columns(mine) == expected


def test_every_column_s4_saw_edr_read_is_there_with_s4s_type(elementary_package):
    mine = {c["name"]: SNOWFLAKE_TYPE[c["type"]] for c in run_macro("it_compat_test_results", {})["columns"]}
    for column, snowflake_type in S4["types"]["elementary_test_results"].items():
        assert mine[column] == snowflake_type, column


def test_the_package_version_is_the_pinned_one(elementary_package):
    assert pinned_version() == "0.26.1"


def test_view_selects_only_from_the_core_view_and_casts_every_column():
    out = run_macro("it_compat_test_results", {})
    sql = out["sql"]
    assert sql.endswith('from "DB"."OBS"."TEST_RESULTS" as t')
    assert "base_events" not in sql.lower() and "event" not in sql.lower().split("from")[1]
    for column in out["columns"]:
        assert f" as {column['name']}" in sql
    assert sql.count("cast(") == len(out["columns"])


def test_unfillable_columns_are_typed_nulls():
    columns = {c["name"]: c["expr"] for c in run_macro("it_compat_test_results", {})["columns"]}
    assert sorted(n for n, e in columns.items() if e is None) == sorted([
        "data_issue_id", "model_unique_id", "database_name", "schema_name", "table_name", "owners", "tags",
        "test_results_query", "test_params", "test_alias", "result_rows"])


def test_compat_views_list_holds_the_elementary_test_view():
    views = run_macro("it_compat_views_render", {})
    assert "ELEMENTARY_TEST_RESULTS" in [v["name"] for v in views]


def test_compat_schema_is_separate_from_the_obs_schema_and_configurable():
    assert run_macro("it_compat_schema", {"schema": "icecore_obs_x"}).endswith('."ICECORE_OBS_X_ELEMENTARY"')
    named = run_macro("it_compat_schema", {}, ["--vars", json.dumps({"icecore": {"compat_schema": "my_edr"}})])
    assert named.endswith('."MY_EDR"')
    assert run_macro("it_compat_schema", {"compat_schema": "x"}).endswith('."X"')


@pytest.mark.parametrize("name", ['x"; drop schema y; --', "a.b", "a b", "x'y", "é"])
def test_compat_schema_name_outside_the_identifier_set_is_a_compile_error(name):
    from helpers.macros import MacroFailed
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        run_macro("it_compat_schema", {"compat_schema": name})
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        run_macro("it_compat_schema", {}, ["--vars", json.dumps({"icecore": {"compat_schema": name}})])


def test_severity_is_dbts_configured_value_not_lower_cased():
    sql = run_macro("it_compat_test_results", {})["sql"]
    assert "lower(t.severity)" not in sql and "t.severity" in sql


def test_setup_creates_compat_views_only_when_asked():
    names = lambda steps: [s["name"] for s in steps]  # noqa: E731
    assert "create_compat_views" not in names(call_macro("_icecore_setup_steps"))
    assert "create_compat_views" not in names(call_macro("_icecore_setup_steps", compat="none"))
    steps = call_macro("_icecore_setup_steps", compat="elementary")
    step, = [s for s in steps if s["name"] == "create_compat_views"]
    assert step["ready"] is True
    assert names(steps).index("create_views") < names(steps).index("create_compat_views")


def test_only_elementary_is_a_compat_target():
    from helpers.macros import MacroFailed
    with pytest.raises(MacroFailed):
        run_macro("it_create_compat_views", {"compat": "datafold"})


def test_setup_refuses_an_unsupported_compat_before_any_step():
    from helpers.macros import MacroFailed
    with pytest.raises(MacroFailed, match="no step was run"):
        run_macro("icecore.setup", {"compat": "datafold"})


# --- sandbox --------------------------------------------------------------------------------------

def seed_test_result(inv, name, status, severity, failures, **extra):
    attrs = {"icecore.kind": "node_result", "dbt.invocation_id": inv, "dbt.node.unique_id": f"test.p.{name}",
             "dbt.node.type": "test", "dbt.node.name": name, "dbt.test.name": name, "dbt.test.type": "generic",
             "dbt.test.column": "id", "dbt.test.severity": severity, "dbt.result.status": status,
             "dbt.test.failures": failures, "dbt.result.message": f"{name} {status}", **extra}
    return {"type": "log", "level": "info", "message": f"test.p.{name} {status}", "attrs": attrs}


@pytest.mark.sandbox
def test_views_are_created_in_the_compat_schema_with_elementarys_types_and_the_core_row_count(sf, elementary_package):
    with obs_schema(sf) as obs:
        compat = f"{obs}_ELEMENTARY"
        obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": obs}})]
        inv = f"compat-{uuid.uuid4().hex}"
        since = snowflake_since(sf)
        items = [seed_test_result(inv, "not_null_a_id", "pass", "ERROR", 0),
                 seed_test_result(inv, "unique_a_id", "fail", "ERROR", 3),
                 seed_test_result(inv, "volume_a", "warn", "WARN", 0, **{
                     "dbt.anomaly.engine": "native", "dbt.anomaly.expected": 100.0, "dbt.anomaly.lower": 90.0,
                     "dbt.anomaly.upper": 110.0, "dbt.anomaly.score": 7.5, "dbt.anomaly.is_anomaly": True})]
        try:
            run_macro("it_compat_emit_items", {"items": items}, obs_vars)
            deadline = time.monotonic() + 300
            run_macro("it_create_views", {"schema": obs}, obs_vars)
            while True:
                core = sf.cursor().execute(
                    f'select count(*) from "{DATABASE}"."{obs}"."TEST_RESULTS" where invocation_id = %s', (inv,)).fetchone()[0]
                if core == 3 or time.monotonic() > deadline:
                    break
                time.sleep(5)
            assert core == 3
            assert not sf.cursor().execute(f"show schemas like '{compat}' in database {DATABASE}").fetchall()  # no compat yet

            created = run_macro("it_create_compat_views", {"schema": obs}, obs_vars)
            assert f'"{DATABASE}"."{compat}"."ELEMENTARY_TEST_RESULTS"' in created

            columns = sf.cursor().execute(
                f"select column_name, data_type from {DATABASE}.information_schema.columns "
                f"where table_schema = '{compat}' and table_name = 'ELEMENTARY_TEST_RESULTS' order by ordinal_position").fetchall()
            assert columns == [(n.upper(), SNOWFLAKE_TYPE[t]) for n, t in table_columns("elementary_test_results")]

            rows = sf.cursor().execute(
                f'select * from "{DATABASE}"."{compat}"."ELEMENTARY_TEST_RESULTS" where invocation_id = %s', (inv,))
            names = [d[0].lower() for d in rows.description]
            by_test = {r[names.index("test_unique_id")]: dict(zip(names, r)) for r in rows.fetchall()}
            assert len(by_test) == core == 3
            fail = by_test["test.p.unique_a_id"]
            assert fail["status"] == "fail" and fail["severity"] == "ERROR" and fail["failures"] == 3
            assert fail["failed_row_count"] == 3 and fail["test_type"] == "dbt_test" and fail["test_sub_type"] == "generic"
            assert fail["column_name"] == "id" and fail["id"] == f"{inv}.test.p.unique_a_id" and fail["detected_at"] is not None
            assert fail["model_unique_id"] is None and fail["other"] is None
            anomaly = by_test["test.p.volume_a"]
            assert anomaly["test_type"] == "anomaly_detection" and anomaly["test_sub_type"] is None
            assert json.loads(anomaly["other"]) == {"engine": "native", "expected": 100.0, "lower": 90.0, "upper": 110.0, "score": 7.5}

            # the whole view counts what the core view counts
            counts = sf.cursor().execute(
                f'select (select count(*) from "{DATABASE}"."{compat}"."ELEMENTARY_TEST_RESULTS"), '
                f'(select count(*) from "{DATABASE}"."{obs}"."TEST_RESULTS")').fetchone()
            assert counts[0] == counts[1]
        finally:
            sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{compat}" cascade')
