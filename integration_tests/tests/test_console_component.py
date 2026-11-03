"""The Console component (prototype D bundled): build files exist, need no network, and render() declares from them."""
import datetime as dt
import decimal
import re
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
BUILD = APP_DIR / "components" / "console" / "build"


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
    assert "icecore-mock-data" in js  # dev mode names the file; the build does not contain it
    assert not (BUILD / "icecore-mock-data.js").exists()


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
