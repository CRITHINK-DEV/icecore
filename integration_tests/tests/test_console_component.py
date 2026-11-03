"""The Console component (prototype D bundled): build files exist, need no network, and render() declares from them."""
import datetime as dt
import decimal
import re
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
BUILD = APP_DIR / "components" / "console" / "build"
SRC = APP_DIR / "components" / "console" / "frontend" / "src"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_build_files_exist():
    assert (BUILD / "index.html").is_file()
    assert (BUILD / "assets" / "console.js").is_file()
    assert (BUILD / "assets" / "console.css").is_file()
    html = (BUILD / "index.html").read_text()
    assert "./assets/console.js" in html and "./assets/console.css" in html  # relative: served under the component URL


def test_build_loads_nothing_from_the_network():
    html = (BUILD / "index.html").read_text()
    css = (BUILD / "assets" / "console.css").read_text()
    assert not re.search(r"""(src|href)=["']https?://""", html)
    assert not re.search(r"""url\(\s*["']?https?://""", css), "fonts and images must be bundled"
    assert not re.search(r"@import", css)
    js = (BUILD / "assets" / "console.js").read_text()
    assert "fonts.googleapis.com" not in js + css and "unpkg.com" not in js and "cdnjs" not in js
    assert not (BUILD / "icecore-mock-data.js").exists()  # the standalone page names it; the build does not ship it


def test_build_takes_no_script_source_from_the_url():
    # A ?mock=<url> loader would run any script on the Streamlit app's authenticated origin.
    js = (BUILD / "assets" / "console.js").read_text()
    assert not re.search(r"""(get|has)\(\s*["']mock["']""", js)
    assert "location.search" not in js
    assert re.search(r"""\.src\s*=\s*["']icecore-mock-data\.js["']|=\s*["']icecore-mock-data\.js["']""", js)



def test_no_filters_action():
    # Python always sends 30 days for all layers; the window and layer filters stay in the browser (review of #128).
    js = (BUILD / "assets" / "console.js").read_text()
    assert not re.search(r"""["']filters["']""", js)
    from lib import console_component

    assert "filters" not in (console_component.__doc__ or "")


def test_each_page_keeps_its_own_window():
    # The prototype's behaviour: every page opens at 15 days, whatever another page chose.
    for page in ("Overview", "Runs", "Cost"):
        src = (SRC / "pages" / f"{page}.jsx").read_text()
        assert "const [win, setWin] = useState('15');" in src, page
    assert "useSticky" not in (SRC / "components" / "ui.jsx").read_text()


def test_tooltips_escape_data_not_their_own_markup():
    # esc() wraps data values only; a tooltip's own <b> must stay markup.
    for page in ("Tests", "Runs"):
        src = (SRC / "pages" / f"{page}.jsx").read_text()
        assert not re.search(r"esc\([^()]*'<", src), page


def test_dev_error_message_uses_text_not_html():
    assert "innerHTML" not in (SRC / "main.jsx").read_text()


def test_lineage_layout_can_run_off_the_main_thread():
    # Large graphs: the dagre layout runs in a worker shipped with the build; only a neighbourhood above LINEAGE_MAX.
    assert (BUILD / "assets" / "layout.worker.js").is_file()
    lineage = (SRC / "pages" / "Lineage.jsx").read_text()
    assert "from 'dagre'" not in lineage and "layoutAsync" in lineage
    m = re.search(r"LINEAGE_MAX = (\d+)", lineage)
    assert m and int(m.group(1)) <= 300


def test_render_drops_non_finite_numbers(monkeypatch):
    # NaN or Infinity in the args makes the browser's JSON.parse fail and blanks the whole Console.
    pytest.importorskip("streamlit")
    import json
    import streamlit as st

    from lib import console_component

    seen = {}
    monkeypatch.setattr(console_component, "component", lambda: (lambda **kw: seen.update(kw)))
    monkeypatch.setattr(st, "session_state", {}, raising=False)
    data = {"runs": [{"credits": float("nan"), "duration_s": float("inf"), "n": [float("-inf"), 1.5, decimal.Decimal("NaN")]}],
            "metrics": [{"value": decimal.Decimal("Infinity"), "expected": decimal.Decimal("2.5")}]}
    console_component.render(data)
    assert seen["data"] == {"runs": [{"credits": None, "duration_s": None, "n": [None, 1.5, None]}],
                            "metrics": [{"value": None, "expected": 2.5}]}
    json.dumps(seen["data"], allow_nan=False)

def test_render_declares_from_the_build_path(monkeypatch):
    st = pytest.importorskip("streamlit")
    import streamlit.components.v1 as components

    from lib import console_component

    calls = {}

    def fake_component(**kwargs):
        calls["kwargs"] = kwargs
        return {"action": "ack", "incident_key": "k1", "nonce": "n1"}

    def fake_declare(name, path=None, url=None):
        calls["declare"] = (name, path, url)
        return fake_component

    monkeypatch.setattr(components, "declare_component", fake_declare)
    monkeypatch.setattr(console_component, "_component", None)
    monkeypatch.setattr(st, "session_state", {}, raising=False)
    data = {"project": "p", "runs": [{"invocation_id": "i", "started_at": dt.datetime(2026, 11, 3, 6), "credits": decimal.Decimal("0.25")}]}

    out = console_component.render(data, height=777, notice="done")

    name, path, url = calls["declare"]
    assert name == "icecore_console" and url is None
    assert Path(path) == BUILD and Path(path, "index.html").is_file()
    kw = calls["kwargs"]
    assert kw["height"] == 777 and kw["notice"] == "done" and kw["key"] == "icecore_console" and kw["default"] is None
    assert kw["data"]["runs"][0] == {"invocation_id": "i", "started_at": "2026-11-03T06:00:00", "credits": 0.25}
    assert out == {"action": "ack", "incident_key": "k1", "nonce": "n1"}
    # the component keeps its last value across reruns: the same nonce is not returned twice
    assert console_component.render(data) is None


def test_render_ignores_empty_values(monkeypatch):
    pytest.importorskip("streamlit")
    import streamlit as st

    from lib import console_component

    monkeypatch.setattr(console_component, "component", lambda: (lambda **kw: None))
    monkeypatch.setattr(st, "session_state", {}, raising=False)
    assert console_component.render({}) is None
