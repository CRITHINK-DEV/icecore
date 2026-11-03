"""The Console's layer list is data-driven (frontend/src/lib/model.js): checked with node, skipped when node is missing."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).parent / "js" / "layers_check.mjs"


@pytest.fixture(scope="module")
def out():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    run = subprocess.run(["node", str(SCRIPT), str(ROOT)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_the_prototype_mock_keeps_bronze_silver_gold(out):
    assert out["mock"]["keys"] == ["bronze", "silver", "gold"]
    assert out["mock"]["labels"] == ["Bronze", "Silver", "Gold"]
    assert out["mock"]["health"] == ["bronze", "silver", "gold"]
    assert out["mock"]["order"] == ["sources", "transient", "bronze", "silver", "gold", "snapshot"]
    assert out["mock"]["goldAnalytics"] == "gold"


def test_a_project_with_its_own_layers_gets_them_upstream_first(out):
    own = out["own"]
    assert own["labels"] == ["Staging", "Marts"]           # seed, source, snapshot, test and null layers are left out
    assert own["health"] == ["bronze", "silver"]           # one health card per layer, accent by position
    assert own["staging"] == "bronze" and own["marts"] == "silver" and own["seed"] == "sources"
    assert own["order"] == ["sources", "transient", "bronze", "silver", "snapshot"]


def test_no_model_layer_falls_back_to_the_prototype_list(out):
    assert out["none"] == {"keys": ["bronze", "silver", "gold"], "labels": ["Bronze", "Silver", "Gold"]}
