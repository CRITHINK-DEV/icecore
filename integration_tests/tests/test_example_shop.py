"""The example project examples/shop (a small shop wired with icecore): it parses on every supported dbt, and, in the
sandbox, one build writes records that the views show (story 63b7919b).

The example is copied to a temporary folder first, with its package path pointing at this checkout, so the tests leave
no dbt_packages/, target/ or package-lock.yml in the repository.
"""
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest
import yaml
from dbt.cli.main import dbtRunner

from helpers.env import placeholder_env
from helpers.macros import IT_DIR
from helpers.run_hooks import DATABASE, icecore_vars, obs_schema

REPO = IT_DIR.parent
EXAMPLE = REPO / "examples" / "shop"


def shop_copy(tmp: Path) -> Path:
    dest = tmp / "shop"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("target", "dbt_packages", "logs", "package-lock.yml"))
    (dest / "packages.yml").write_text(yaml.safe_dump({"packages": [{"local": str(REPO)}]}))
    return dest


def dbt(project: Path, args: list[str], dbt_vars: dict | None = None):
    full = args + ["--project-dir", str(project), "--profiles-dir", str(project)]
    if args[0] != "deps":
        full += ["--target-path", str(project / "target")]
    if dbt_vars:
        full += ["--vars", json.dumps(dbt_vars)]
    with placeholder_env():
        return dbtRunner().invoke(full)


# --- offline ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def project(tmp_path_factory):
    path = shop_copy(tmp_path_factory.mktemp("example"))
    assert dbt(path, ["deps"]).success
    return path


def test_example_parses_with_both_hooks_and_the_icecore_tests(project):
    res = dbt(project, ["parse"])
    assert res.success, res.exception
    manifest = json.load(open(project / "target" / "manifest.json"))
    nodes = manifest["nodes"]
    kinds = {}
    for n in nodes.values():
        kinds.setdefault(n["resource_type"], []).append(n["name"])
    assert sorted(kinds["model"]) == ["dim_customers", "fct_orders", "stg_customers", "stg_orders"]
    assert sorted(kinds["seed"]) == ["raw_customers", "raw_orders"] and kinds["snapshot"] == ["customers_snapshot"]
    tests = [n["name"] for n in nodes.values() if n["resource_type"] == "test"]
    assert sum(t.startswith("icecore_freshness") for t in tests) == 2 and sum(t.startswith("icecore_volume") for t in tests) == 2, tests
    assert any(s["freshness"]["warn_after"] for s in manifest["sources"].values() if s["name"] == "raw_orders")
    hooks = json.dumps([manifest["nodes"][k]["raw_code"] for k in nodes if k.startswith("operation.")])
    assert "icecore.start_run()" in hooks and "icecore.end_run(results)" in hooks, hooks


def test_example_uses_only_merged_features(project):
    cfg = yaml.safe_load((EXAMPLE / "dbt_project.yml").read_text())
    icecore = cfg["vars"]["icecore"]
    for coming in ("cost", "alerts", "root_cause", "anomaly_engine", "shadow_engine"):
        assert coming not in icecore, f"{coming} is not merged or not used: it belongs under 'coming' in the README"


def test_the_failing_test_toggle_defaults_to_off():
    assert yaml.safe_load((EXAMPLE / "dbt_project.yml").read_text())["vars"]["shop_fail"] is False


# --- sandbox ----------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_one_build_writes_records_the_views_show(sf, tmp_path):
    rid = uuid.uuid4().hex.upper()
    shop_schema = f"SHOP_{rid}"
    project = shop_copy(tmp_path)
    cur = sf.cursor()
    os.environ["SHOP_SCHEMA"] = shop_schema
    try:
        assert dbt(project, ["deps"]).success
        with obs_schema(sf) as obs:
            dbt_vars = {**icecore_vars(obs), "shop_fail": True}
            assert dbt(project, ["run-operation", "create_views", "--args", json.dumps({"schema": obs})],
                       icecore_vars(obs)).success
            # the raw tables are loaded before the build, as an EL tool would: sources have no dependency on the seeds
            assert dbt(project, ["seed"], dbt_vars).success
            res = dbt(project, ["build", "--exclude", "resource_type:seed"], dbt_vars)
            assert not res.success  # the toggled test fails; everything else builds
            run_results = json.load(open(project / "target" / "run_results.json"))
            statuses = {r["unique_id"].split(".")[2] if r["unique_id"].startswith("test.") else r["unique_id"].split(".")[-1]: r["status"]
                        for r in run_results["results"] if not r["unique_id"].startswith("operation.")}
            invocation_id = run_results["metadata"]["invocation_id"]
            assert sum(1 for s in statuses.values() if s in ("fail", "error")) == 1, statuses

            def rows(view, where, min_rows, **params):
                deadline = time.monotonic() + 300
                while True:
                    got = cur.execute(f'select * from "{DATABASE}"."{obs}"."{view}" where {where}', params).fetchall()
                    if len(got) >= min_rows or time.monotonic() > deadline:
                        return got
                    time.sleep(5)

            test_rows = rows("TEST_RESULTS", "invocation_id = %(i)s", 8, i=invocation_id)
            failed = [r for r in test_rows if r[7] == "fail"]
            assert len(failed) == 1 and failed[0][3] and "not_null" in failed[0][2], test_rows
            assert len(test_rows) >= 8
            metrics = rows("METRICS", "invocation_id = %(i)s", 4, i=invocation_id)
            assert sorted({r[3] for r in metrics}) == ["freshness_lag_seconds", "row_count"], metrics
            edges = rows("LINEAGE", "invocation_id = %(i)s", 1, i=invocation_id)
            pairs = {(r[2].split(".")[-1], r[3].split(".")[-1]) for r in edges}
            assert ("stg_orders", "fct_orders") in pairs and ("stg_customers", "dim_customers") in pairs, pairs
    finally:
        os.environ.pop("SHOP_SCHEMA", None)
        cur.execute(f'drop schema if exists "{DATABASE}"."{shop_schema}"')
