"""The Overview layer health strip follows the number of layers (frontend/src/lib/strip.js, pages/Overview.jsx).

With 1 to 3 layers it renders as prototype D did (class "health", a chevron between every card). With 4 or more,
cards keep their 3-per-row width and wrap onto further rows; a row starts without a chevron."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).parent / "js" / "strip_check.mjs"
SRC = ROOT / "streamlit" / "components" / "console" / "frontend" / "src"
BUILD = ROOT / "streamlit" / "components" / "console" / "build"


@pytest.fixture(scope="module")
def out():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    run = subprocess.run(["node", str(SCRIPT), str(ROOT)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("n", [1, 2, 3])
def test_one_to_three_layers_render_as_the_prototype(out, n):
    assert out["perRow"] == 3
    assert out["out"][str(n)] == {"cls": "health", "joins": [False] + [True] * (n - 1)}


@pytest.mark.parametrize("n,joins", [
    (4, [False, True, True, False]),
    (6, [False, True, True, False, True, True]),
    (7, [False, True, True, False, True, True, False]),
])
def test_four_or_more_layers_wrap_three_per_row(out, n, joins):
    assert out["out"][str(n)] == {"cls": "health many", "joins": joins}


def test_overview_uses_the_strip_layout():
    src = (SRC / "pages" / "Overview.jsx").read_text()
    assert "className={stripClass(health.length)}" in src
    assert "stripJoin(i, health.length)" in src


def test_the_wrap_rule_is_an_override_and_the_prototype_css_stays_verbatim():
    assert re.search(r"\.health\.many\{[^}]*row-gap:14px", (SRC / "bundle.css").read_text())
    assert ".health{display:grid;grid-template-columns:1fr 28px 1fr 28px 1fr;" in (SRC / "console.css").read_text()
    css = (BUILD / "assets" / "console.css").read_text()
    assert re.search(r"\.health\.many\{[^}]*row-gap:14px", css), "rebuild the frontend (npm run build)"
