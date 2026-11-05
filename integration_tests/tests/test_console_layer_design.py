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


# --- review of #138 ---------------------------------------------------------------------------------------------

def test_an_empty_standard_means_no_standard_and_flags_nothing(out):
    assert out["emptyStd"]["issues"] == [] and out["emptyStd"]["cap"] is None


def test_the_raw_layer_is_compared_with_the_standard(out):
    # gold_analytics is its own name in the standard: never folded into gold
    # the reviewer's case: a standard of [bronze, gold_analytics] does not flag the gold_analytics model
    assert out["rawStd"] == [{"id": "model.p.fct", "kind": "standard",
                              "reason": "Layer \u201cgold\u201d is not one of bronze, gold_analytics"}]
    assert out["rawStdOut"] == [{"id": "model.p.agg", "kind": "standard",
                                 "reason": "Layer \u201cgold_analytics\u201d is not one of bronze, gold"}]


def test_the_offending_edge_cannot_flip_the_layer_order(out):
    # stg_a -> fct (marts), and stg_b (staging) reads fct: stg_b is the offender, with or without a standard
    want = [{"id": "model.p.stg_b", "kind": "backward", "reason": "Reads fct, from Marts, a downstream layer"}]
    assert out["reviewer"] == want
    assert out["reviewerStd"] == want


def test_a_declared_standard_gives_the_order(out):
    # the standard lists marts first: then fct, which reads staging, is the one reading downstream
    assert out["reviewerStdReversed"] == [{"id": "model.p.fct", "kind": "backward",
                                           "reason": "Reads stg_a, from Staging, a downstream layer"}]


def test_a_project_without_issues_shows_no_layer_design_line():
    # the owner approved only the line that shows issues: with none, the Overview looks as before
    src = (ROOT / "streamlit" / "components" / "console" / "frontend" / "src" / "pages" / "Overview.jsx").read_text()
    assert "no issues" not in src
    assert "if (!HAS_MODEL_LAYERS || !LAYER_ISSUES.length) return null;" in src
    js = (ROOT / "streamlit" / "components" / "console" / "build" / "assets" / "console.js").read_text()
    assert "no issues" not in js, "rebuild the frontend (npm run build)"
