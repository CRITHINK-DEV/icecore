"""Layer design checks of the Console (frontend/src/lib/model.js, story cfc1e7fc): checked with node, skipped without.

Three checks inside one project: a backward dependency (a model reads a model of a layer further downstream, layers
ordered upstream first as setLayers orders them), a model with no layer (directly under models/), and, only when
var icecore.layer_standard is declared, a model whose layer is not in it. transient, seed, source and snapshot are
never flagged. The flags inform only: no score, status or incident changes."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).parent / "js" / "layer_design_check.mjs"


@pytest.fixture(scope="module")
def out():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    run = subprocess.run(["node", str(SCRIPT), str(ROOT)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_a_clean_project_has_no_issue(out):
    assert out["clean"]["issues"] == [] and out["clean"]["hasLayers"] is True
    assert out["clean"]["seq"] == ["staging", "intermediate", "marts"]
    assert out["cleanStd"]["issues"] == []          # the standard is compared case-insensitively


def test_a_backward_edge_is_flagged_once_per_model(out):
    assert out["backward"]["issues"] == [{
        "id": "model.p.stg_b", "kind": "backward",
        "reason": "Reads fct, from Marts, and int_a, from Intermediate, downstream layers"}]


def test_a_model_with_no_layer_is_flagged_when_it_ran(out):
    assert out["nolayer"]["issues"] == [{
        "id": "model.p.root_model", "kind": "nolayer", "reason": "Sits directly under models/, in no layer folder"}]


def test_a_model_outside_the_standard_is_flagged_only_when_a_standard_is_declared(out):
    assert out["standard"]["issues"] == [{
        "id": "model.p.fx", "kind": "standard",
        "reason": "Layer “exports” is not one of staging, intermediate, marts"}]
    assert out["nostandard"]["issues"] == []
    assert out["badStandard"] is None                 # not a list: no standard


def test_transient_seed_source_and_snapshot_are_never_flagged(out):
    for case in ("clean", "backward", "nolayer", "standard"):
        flagged = {i["id"] for i in out[case]["issues"]}
        assert not flagged & {"model.p.tmp", "source.p.raw", "seed.p.s", "snapshot.p.sn"}, case


def test_no_model_layer_means_no_check(out):
    assert out["nolayers"]["hasLayers"] is False and out["nolayers"]["issues"] == []


def test_the_flags_inform_only(out):
    assert out["informOnly"]["same"] is True          # same health and status with and without a standard
