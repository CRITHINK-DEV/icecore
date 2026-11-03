"""UC7 layer and freshness limit in the views.

Layer: node_attributes writes `dbt.node.fqn` (dotted) and `dbt.node.layer` (first folder under models/ for a model,
renamed by var icecore.layers; the resource type for seeds, snapshots, sources). Lineage records carry
`dbt.lineage.node_fqn` / `dbt.lineage.node_layer`. LINEAGE, LINEAGE_LATEST (parent_layer, child_layer) and
NODE_EXECUTIONS (layer) expose them. Threshold: `icecore.freshness` passes max_lag in seconds to emit_metric, which
writes `icecore.metric.threshold`; METRICS exposes it as `threshold`. Records without the keys read as null.

Offline tests render macros (no connection). The sandbox test runs a real `dbt build` of the layer_* models (models/bronze, gold, brz) in a run_id
obs schema and reads the three views back.
"""
import json
import os
import uuid

import pytest
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.macros import call_macro, run_macro
from helpers.run_hooks import (
    obs_schema, run_with_hooks, snowflake_since,
)

PROJECT = "icecore_integration_tests"
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES").upper()
REL = {"database": "icecore_sandbox", "schema": "spikes", "identifier": "orders"}


def node(fqn, resource_type="model"):
    return {"unique_id": f"{resource_type}.p.m", "resource_type": resource_type, "name": fqn[-1], "fqn": fqn,
            "config": {"materialized": "table"}}


def attrs_of(fqn, resource_type="model", layers=None):
    extra = ["--vars", json.dumps({"icecore": {"layers": layers}})] if layers is not None else None
    return run_macro("it_node_attributes", {"node": node(fqn, resource_type)}, extra)


# --- offline: attribute extraction ------------------------------------------------------------------

def test_model_fqn_is_dotted_and_layer_is_the_first_folder():
    a = attrs_of(["shop", "bronze", "stripe", "orders"])
    assert a["dbt.node.fqn"] == "shop.bronze.stripe.orders"
    assert a["dbt.node.layer"] == "bronze"


def test_a_two_part_fqn_has_a_fqn_and_no_layer():
    a = attrs_of(["shop", "orders"])
    assert a["dbt.node.fqn"] == "shop.orders"
    assert "dbt.node.layer" not in a


@pytest.mark.parametrize("resource_type", ["seed", "snapshot", "source"])
def test_seeds_snapshots_and_sources_have_their_type_as_layer(resource_type):
    assert attrs_of(["shop", "folder", "x"], resource_type)["dbt.node.layer"] == resource_type
    assert attrs_of(["shop", "x"], resource_type)["dbt.node.layer"] == resource_type


def test_a_test_node_has_no_layer():
    assert "dbt.node.layer" not in attrs_of(["shop", "unique_orders_id"], "test")


def test_var_layers_renames_a_folder_and_leaves_the_others():
    layers = {"brz": "bronze"}
    assert attrs_of(["shop", "brz", "orders"], layers=layers)["dbt.node.layer"] == "bronze"
    assert attrs_of(["shop", "gold", "orders"], layers=layers)["dbt.node.layer"] == "gold"


def test_a_real_model_of_the_project_gets_its_folder_and_the_mapping():
    uid = f"model.{PROJECT}.layer_bronze"
    assert run_macro("it_node_attributes", {"unique_id": uid})["dbt.node.layer"] == "bronze"
    extra = ["--vars", json.dumps({"icecore": {"layers": {"bronze": "silver"}}})]
    a = run_macro("it_node_attributes", {"unique_id": uid}, extra)
    assert a["dbt.node.layer"] == "silver" and a["dbt.node.fqn"] == f"{PROJECT}.bronze.layer_bronze"


def test_lineage_records_carry_the_fqn_and_layer_of_their_nodes():
    records = run_macro("it_end_run_render", {"results": []})["records"]
    log = next(r for r in records if r["attrs"].get("icecore.kind") == "lineage")
    a, b = f"model.{PROJECT}.hooks_a", f"model.{PROJECT}.hooks_b"
    fqns = json.loads(log["attrs"]["dbt.lineage.node_fqn"])
    layers = json.loads(log["attrs"]["dbt.lineage.node_layer"])
    assert fqns[a] == f"{PROJECT}.hooks.hooks_a"
    assert layers[a] == layers[b] == "hooks"



def test_a_source_parent_has_the_type_as_layer_in_the_lineage_record():
    graph = run_macro("it_lineage_edges", {})
    source_ids = {u for e in graph for u in e if u.startswith("source.")}
    records = run_macro("it_end_run_render", {"results": []})["records"]
    layers = {}
    for r in records:
        if r["attrs"].get("icecore.kind") == "lineage" and "dbt.lineage.node_layer" in r["attrs"]:
            layers.update(json.loads(r["attrs"]["dbt.lineage.node_layer"]))
    assert source_ids, "the fixture project has no source in an edge"
    assert all(layers[u] == "source" for u in source_ids)


def test_lineage_layers_follow_the_var():
    extra = ["--vars", json.dumps({"icecore": {"layers": {"hooks": "bronze"}}})]
    records = run_macro("it_end_run_render", {"results": []}, extra)["records"]
    log = next(r for r in records if r["attrs"].get("icecore.kind") == "lineage")
    assert json.loads(log["attrs"]["dbt.lineage.node_layer"])[f"model.{PROJECT}.hooks_a"] == "bronze"


def test_a_synthetic_edge_the_graph_does_not_know_has_no_node_keys():
    out = run_macro("it_lineage_render", {"edges": [["model.x.a", "model.x.b"]]})
    attrs = out["records"][0]["attrs"]
    assert "dbt.lineage.node_fqn" not in attrs and "dbt.lineage.node_layer" not in attrs


@pytest.mark.parametrize("cap_kb", [1, 2, 4])
def test_node_maps_stay_under_the_payload_cap_and_stay_valid_json(cap_kb):
    n = 22 * 2
    uids = [f"model.{PROJECT}.m_{i:03d}" for i in range(n)]
    info = {u: {"fqn": f"{PROJECT}.some_folder.m_{i:03d}",
                "layer": "some_layer"} for i, u in enumerate(uids)}
    edges = [[uids[2 * i], uids[2 * i + 1]] for i in range(22)]
    out = run_macro("it_lineage_render", {"edges": edges, "info": info},
                    ["--vars", json.dumps({"icecore": {"payload_cap_kb": cap_kb}})])
    assert len(out["records"]) > 1
    seen = set()
    for record, size in zip(out["records"], out["sizes"]):
        attrs = record["attrs"]
        assert size <= cap_kb * 1024 and "icecore.truncated" not in attrs, (size, attrs.get("icecore.truncated_keys"))
        fqns = json.loads(attrs["dbt.lineage.node_fqn"])
        layers = json.loads(attrs["dbt.lineage.node_layer"])
        assert set(fqns) == set(layers)
        seen |= set(fqns)
    assert seen == set(uids)


def test_a_versioned_model_in_models_has_no_layer_not_its_name():
    versioned = {**node(["shop", "orders", "v2"]), "version": 2}
    a = run_macro("it_node_attributes", {"node": versioned})
    assert a["dbt.node.fqn"] == "shop.orders" and "dbt.node.layer" not in a
    in_folder = {**node(["shop", "bronze", "orders", "v2"]), "version": "2"}
    a = run_macro("it_node_attributes", {"node": in_folder})
    assert a["dbt.node.fqn"] == "shop.bronze.orders" and a["dbt.node.layer"] == "bronze"


def test_a_layers_var_that_is_not_a_mapping_is_ignored_and_doctor_reports_it():
    extra = ["--vars", json.dumps({"icecore": {"layers": ["bronze"]}})]
    assert attrs_of(["shop", "bronze", "orders"], layers=["bronze"])["dbt.node.layer"] == "bronze"
    problem = run_macro("it_call", {"name": "_icecore_layers_problem", "kwargs": {}}, extra)
    assert problem and "must be a mapping" in problem
    assert run_macro("it_call", {"name": "_icecore_layers_problem", "kwargs": {}}) is None
    caps = {"database": "DB", "event_table": "DB.T.EVENTS", "log_level": "INFO", "trace_level": "ALWAYS"}
    probes = {"ICECORE_EMIT": "ok 1", "ICECORE_EMIT_RUN": "ok spans=0 logs=1"}
    report = run_macro("it_call", {"name": "_icecore_doctor_report",
                                   "kwargs": {"capabilities": caps, "probes": probes, "count": 3}}, extra)
    check = next(c for c in report["checks"] if c["name"] == "layers")
    assert not check["ok"] and "icecore.layers" in check["detail"]


# --- offline: threshold ---------------------------------------------------------------------------

def render(**kwargs) -> dict:
    return run_macro("it_emit_metric_render", {"rel": REL, "metric": "freshness_lag_seconds", "value": 12.5, **kwargs})


def test_threshold_is_written_as_a_number_under_its_key():
    attrs = render(threshold=7200.0)["records"][0]["attrs"]
    assert attrs["icecore.metric.threshold"] == 7200.0


def test_no_threshold_or_a_non_number_leaves_the_key_out():
    assert "icecore.metric.threshold" not in render()["records"][0]["attrs"]
    assert "icecore.metric.threshold" not in render(threshold="2h")["records"][0]["attrs"]


def test_freshness_passes_its_max_lag_in_seconds_to_emit_metric():
    text = open(os.path.join(os.path.dirname(__file__), "..", "..", "tests", "generic", "freshness.sql")).read()
    assert "threshold=max_lag_seconds" in text


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built(sf):
    run_id = uuid.uuid4().hex[:12]
    since = snowflake_since(sf)
    with obs_schema(sf) as schema:
        try:
            res, _ = run_with_hooks(["build", "--select", "layer_bronze", "layer_gold", "layer_brz"], run_id, schema,
                                    layers={"brz": "silver"})
            assert res.success, res.exception
            wait_for_events(
                sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                    " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                    " and record_attributes:\"icecore.kind\"::string = 'run_end'",
                {"db": DATABASE, "schema": schema}, min_rows=1, since=since, settle_s=15)
            read = {}
            for view in ("node_executions", "lineage", "metrics"):
                sql = call_macro(f"{view}_view_sql", obs_schema=schema)
                read[view] = sf.cursor(DictCursor).execute(sql).fetchall()
            yield {"run_id": run_id, **read}
        finally:
            for kind, model in (("table", "layer_bronze"), ("view", "layer_gold"), ("view", "layer_brz")):
                sf.cursor().execute(f'drop {kind} if exists "{DATABASE}"."{SCHEMA}"."{model.upper()}_{run_id.upper()}"')


@pytest.mark.sandbox
def test_node_executions_and_lineage_show_the_layer(built):
    by_name = {r["NAME"].lower(): r for r in built["node_executions"] if r["TYPE"] == "model"}
    # the folder is the layer; the var maps brz to silver and leaves bronze and gold
    assert by_name["layer_bronze"]["LAYER"] == "bronze"
    assert by_name["layer_gold"]["LAYER"] == "gold"
    assert by_name["layer_brz"]["LAYER"] == "silver"
    edge = next(r for r in built["lineage"] if r["PARENT_UNIQUE_ID"].endswith(".layer_bronze")
                and r["CHILD_UNIQUE_ID"].endswith(".layer_gold"))
    assert edge["PARENT_LAYER"] == "bronze" and edge["CHILD_LAYER"] == "gold"


@pytest.mark.sandbox
def test_metrics_carry_the_freshness_limit(built):
    rows = [r for r in built["metrics"] if r["METRIC"] == "freshness_lag_seconds"
            and r["RELATION"].endswith(f'LAYER_BRONZE_{built["run_id"].upper()}"')]
    assert rows and all(r["THRESHOLD"] == 7200 for r in rows), rows
