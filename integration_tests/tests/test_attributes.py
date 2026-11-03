"""UC1 attribute namespace: icecore.base_attributes() and icecore.node_attributes(node, result).

The macros are rendered through the `it_base_attributes` / `it_node_attributes` wrappers in
integration_tests/macros/test_support/it_attributes.sql, which log the dict as `icecore_it_result:<json>`.
Offline tests render inside `dbt run-operation`; the sandbox test renders inside a real `dbt build`
(the singular test tests_dbt/it_attributes_build.sql, enabled only by the `it_attributes_build` var).
"""
import json
import os

import pytest
import yaml

from helpers.macros import IT_DIR, RESULT_PREFIX, install_deps, invoke, run_macro

REPO = IT_DIR.parent
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")


def package_version() -> str:
    return str(yaml.safe_load(open(REPO / "dbt_project.yml"))["version"])


def run_capturing(args: list[str]) -> tuple[list, set, object]:
    """Run dbt; return (values logged with RESULT_PREFIX, invocation ids seen on the events, result)."""
    install_deps()
    seen, invocation_ids = [], set()

    def collect(event) -> None:
        invocation_ids.add(event.info.invocation_id)
        msg = getattr(event.info, "msg", "") or ""
        if event.info.name == "JinjaLogInfo" and msg.startswith(RESULT_PREFIX):
            seen.append(json.loads(msg[len(RESULT_PREFIX):]))

    res = invoke(args, [collect])
    return seen, invocation_ids, res


# --- offline: rendering only, no connection ------------------------------------------------------

def test_base_attributes_carry_the_invocation():
    seen, invocation_ids, res = run_capturing(["run-operation", "it_base_attributes"])
    assert res.success, res.exception
    attrs = seen[-1]
    assert len(invocation_ids) == 1
    assert attrs["dbt.invocation_id"] == invocation_ids.pop()
    assert attrs["dbt.command"] == "run-operation"
    assert attrs["dbt.target"] == "sandbox"
    assert attrs["dbt.project"] == "icecore_integration_tests"
    assert attrs["dbt.runtime"] == "core"


def test_base_attributes_carry_the_package_version():
    attrs = run_macro("it_base_attributes", {})
    assert attrs["icecore.version"] == package_version()
    assert attrs["icecore.schema_version"] == 1
    assert isinstance(attrs["icecore.schema_version"], int)


@pytest.mark.parametrize("key", ["DBT_CLOUD_RUN_ID", "DBT_CLOUD_PROJECT_ID"])
def test_runtime_is_cloud_when_dbt_cloud_variables_are_set(monkeypatch, key):
    monkeypatch.setenv(key, "12345")
    assert run_macro("it_base_attributes", {})["dbt.runtime"] == "cloud"


@pytest.mark.parametrize("project_dir, profiles_dir, expected", [
    # measured inside EXECUTE DBT PROJECT (spikes/S13-dbt-projects.md, results/s13.json, key invocation_args)
    ("/tmp/dbt", "/tmp/dbt_effective_profiles_tlp_g3gd", "snowflake_native"),
    # a local run: either signal alone is not enough
    ("/Users/me/shop", "/Users/me/.dbt", "core"),
    ("/tmp/dbt", "/Users/me/.dbt", "core"),
    ("/Users/me/shop", "/tmp/dbt_effective_profiles_x", "core"),
])
def test_runtime_is_snowflake_native_only_inside_execute_dbt_project(project_dir, profiles_dir, expected):
    seen, _, res = run_capturing(["run-operation", "it_runtime",
                                  "--args", json.dumps({"project_dir": project_dir, "profiles_dir": profiles_dir})])
    assert res.success, res.exception
    assert seen[-1] == expected


def test_model_node_attributes():
    attrs = run_macro("it_node_attributes", {"unique_id": "model.icecore_integration_tests.rc_a"})
    assert attrs["dbt.node.unique_id"] == "model.icecore_integration_tests.rc_a"
    assert attrs["dbt.node.type"] == "model"
    assert attrs["dbt.node.name"] == "rc_a"
    assert attrs["dbt.node.materialization"] == "ephemeral"
    assert attrs["dbt.node.relation"] == f'"{DATABASE.upper()}"."{SCHEMA.upper()}"."RC_A"'
    assert not any(k.startswith("dbt.result.") for k in attrs)


def test_source_node_relation_uses_its_identifier():
    attrs = run_macro("it_node_attributes", {"unique_id": "source.icecore_integration_tests.rc_raw.orders"})
    assert attrs["dbt.node.type"] == "source"
    assert attrs["dbt.node.relation"] == f'"{DATABASE.upper()}"."RC_RAW"."ORDERS"'


def test_quoted_identifier_keeps_its_case_and_doubles_quotes():
    node = {"unique_id": "model.p.m", "resource_type": "model", "name": "m", "database": "db", "schema": "sch",
            "alias": 'Mixed "Case"', "config": {"materialized": "table", "quoting": {"identifier": True}}}
    attrs = run_macro("it_node_attributes", {"node": node})
    assert attrs["dbt.node.relation"] == '"DB"."SCH"."Mixed ""Case"""'


def test_test_node_attributes():
    node_id = next(uid for uid in manifest_nodes() if uid.startswith("test.icecore_integration_tests.not_null_rc_c_id"))
    attrs = run_macro("it_node_attributes", {"unique_id": node_id})
    assert attrs["dbt.node.type"] == "test"
    assert attrs["dbt.test.name"] == "not_null"
    assert attrs["dbt.test.type"] == "generic"
    assert attrs["dbt.test.column"] == "id"
    assert attrs["dbt.test.severity"] == "ERROR"


def _test_node(**kw):
    node = {"unique_id": "test.p.t", "name": "t", "resource_type": "test", "config": {"severity": "ERROR"},
            "test_metadata": {"name": "not_null"}}
    node.update(kw)
    return node


def _attached(node):
    return run_macro("it_node_attributes", {"node": node}).get("dbt.test.attached_node")


def test_generic_test_names_its_attached_node():
    assert _attached(_test_node(attached_node="model.p.orders", depends_on={"nodes": ["model.p.orders"]})) == "model.p.orders"


def test_attached_node_wins_over_depends_on():
    # a relationships test depends on two models; attached_node says which one it tests
    node = _test_node(attached_node="model.p.orders", depends_on={"nodes": ["model.p.orders", "model.p.customers"]})
    assert _attached(node) == "model.p.orders"


def test_source_test_names_the_source():
    node = _test_node(attached_node="source.p.raw.orders", depends_on={"nodes": ["source.p.raw.orders"]})
    assert _attached(node) == "source.p.raw.orders"


def test_without_attached_node_the_single_model_or_source_in_depends_on_is_used():
    assert _attached(_test_node(depends_on={"nodes": ["model.p.orders"], "macros": ["macro.p.m"]})) == "model.p.orders"
    assert _attached(_test_node(depends_on={"nodes": ["source.p.raw.orders"]})) == "source.p.raw.orders"


def test_singular_test_over_several_nodes_has_no_attached_node():
    singular = {"unique_id": "test.p.s", "name": "s", "resource_type": "test", "config": {},
                "depends_on": {"nodes": ["model.p.orders", "model.p.customers"]}}
    attrs = run_macro("it_node_attributes", {"node": singular})
    assert "dbt.test.attached_node" not in attrs and attrs["dbt.test.type"] == "singular"
    assert _attached(_test_node(depends_on={"nodes": []})) is None
    assert _attached(_test_node()) is None


def test_singular_test_over_one_model_names_it():
    singular = {"unique_id": "test.p.s", "name": "s", "resource_type": "test", "config": {},
                "depends_on": {"nodes": ["model.p.orders"]}}
    assert _attached(singular) == "model.p.orders"


def test_non_test_nodes_have_no_attached_node():
    node = {"unique_id": "model.p.m", "name": "m", "resource_type": "model", "config": {},
            "depends_on": {"nodes": ["model.p.orders"]}}
    assert _attached(node) is None


def test_result_values_stay_typed():
    result = {"status": "success", "execution_time": 4.2, "message": "SUCCESS 1200",
              "adapter_response": {"rows_affected": 1200, "query_id": "01c5a039-0000-0000-0000-000000000001"}}
    attrs = run_macro("it_node_attributes", {"unique_id": "model.icecore_integration_tests.rc_a", "result": result})
    assert attrs["dbt.result.rows_affected"] == 1200
    assert isinstance(attrs["dbt.result.rows_affected"], int)
    assert attrs["dbt.result.execution_time_s"] == 4.2
    assert isinstance(attrs["dbt.result.execution_time_s"], float)
    assert attrs["dbt.result.status"] == "success"
    assert attrs["dbt.result.message"] == "SUCCESS 1200"
    assert attrs["snow.query.id"] == "01c5a039-0000-0000-0000-000000000001"


def test_numeric_strings_become_numbers_and_missing_values_are_left_out():
    result = {"status": "fail", "failures": "3", "execution_time": "0.5", "message": None,
              "adapter_response": {"rows_affected": "1200"}}
    attrs = run_macro("it_node_attributes", {"unique_id": "model.icecore_integration_tests.rc_a", "result": result})
    assert attrs["dbt.result.rows_affected"] == 1200 and isinstance(attrs["dbt.result.rows_affected"], int)
    assert attrs["dbt.result.execution_time_s"] == 0.5
    assert attrs["dbt.test.failures"] == 3 and isinstance(attrs["dbt.test.failures"], int)
    assert "dbt.result.message" not in attrs
    assert "snow.query.id" not in attrs


def manifest_nodes() -> dict:
    install_deps()
    assert invoke(["parse"]).success
    return json.load(open(IT_DIR / "target" / "manifest.json"))["nodes"]


# --- sandbox --------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_base_attributes_inside_a_dbt_build(dbt):
    seen, invocation_ids, res = run_capturing(
        ["build", "--select", "it_attributes_build", "--vars", json.dumps({"it_attributes_build": True})])
    assert res.success, res.exception
    attrs = seen[-1]
    assert len(invocation_ids) == 1
    assert attrs["dbt.invocation_id"] == invocation_ids.pop()
    assert attrs["dbt.command"] == "build"
    assert attrs["icecore.version"] == package_version()
    assert attrs["icecore.schema_version"] == 1
