"""The host page (streamlit_app.py): one full-page Console, build and render patched.

Needs streamlit (not in the dbt venvs, so it skips there):
    cd integration_tests && $SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_host.py
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
APP = str(APP_DIR / "streamlit_app.py")
KEY = "a" * 32
DATA = {"project": "demo", "nodes": [], "errors": [],
        "incidents": [{"incident_key": KEY, "state": "open", "acknowledged_by": None}]}


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    import streamlit as st
    st.cache_data.clear()


class Calls:
    def __init__(self, monkeypatch, actions=(), build=None):
        from lib import ack, console_component, console_data
        self.builds, self.renders, self.acks = [], [], []
        self.actions = list(actions)

        def fake_build(days=30, layer=None, now=None):
            self.builds.append(days)
            if build:
                return build()
            return dict(DATA)

        def fake_render(data, *, height=900, notice=None, key="icecore_console"):
            self.renders.append({"data": data, "notice": notice, "height": height})
            return self.actions.pop(0) if self.actions else None

        def fake_ack(key, layer=None):
            self.acks.append(key)
            return ack.AckResult(ack.OK, "Incident acknowledged.")

        monkeypatch.setattr(console_data, "build", fake_build)
        monkeypatch.setattr(console_component, "render", fake_render)
        monkeypatch.setattr(ack, "acknowledge", fake_ack)


def run(monkeypatch, **kw):
    calls = Calls(monkeypatch, **kw)
    at = AppTest.from_file(APP, default_timeout=30).run()
    return at, calls


def test_renders_the_console_with_30_days_of_data(monkeypatch):
    at, calls = run(monkeypatch)
    assert not at.exception
    assert calls.builds == [30]
    assert calls.renders[0]["data"]["project"] == "demo" and calls.renders[0]["notice"] is None


def test_build_is_cached_between_runs(monkeypatch):
    at, calls = run(monkeypatch)
    at.run()
    assert calls.builds == [30] and len(calls.renders) == 2


def test_ack_calls_acknowledge_once_clears_the_cache_and_shows_the_notice_once(monkeypatch):
    key = "a" * 32
    at, calls = run(monkeypatch, actions=[{"action": "ack", "incident_key": key, "nonce": "1"}])
    assert not at.exception
    assert calls.acks == [key]
    assert calls.builds == [30, 30]                       # cache cleared, rebuilt after the rerun
    assert [r["notice"] for r in calls.renders] == [None, "Incident acknowledged."]
    at.run()                                              # the notice is for the next run only
    assert calls.renders[-1]["notice"] is None and calls.acks == [key]


def test_refresh_clears_the_cache_and_reruns(monkeypatch):
    at, calls = run(monkeypatch, actions=[{"action": "refresh", "nonce": "1"}])
    assert not at.exception
    assert calls.builds == [30, 30] and len(calls.renders) == 2 and calls.acks == []


def test_any_other_action_is_a_no_op(monkeypatch):
    at, calls = run(monkeypatch, actions=[{"action": "filters", "days": 7, "layer": "marts", "nonce": "1"}])
    assert not at.exception
    assert calls.builds == [30] and len(calls.renders) == 1 and calls.acks == []


def test_build_exception_still_renders_with_the_error(monkeypatch):
    def boom():
        raise RuntimeError("no session")

    at, calls = run(monkeypatch, build=boom)
    assert not at.exception
    data = calls.renders[0]["data"]
    assert data["errors"] == [{"view": "app", "error": "no session"}]
    assert data["nodes"] == [] and data["runs"] == [] and data["incidents"] == []


def test_page_is_wide_and_hides_the_streamlit_chrome():
    text = Path(APP).read_text()
    assert 'layout="wide"' in text
    for hidden in ("stHeader", "stToolbar", "stSidebar"):
        assert hidden in text


def test_ack_shows_the_incident_acknowledged_until_a_build_does(monkeypatch):
    at, calls = run(monkeypatch, actions=[{"action": "ack", "incident_key": KEY, "nonce": "1"}])
    assert not at.exception
    shown = calls.renders[-1]["data"]["incidents"][0]
    assert (shown["state"], shown["acknowledged_by"]) == ("acknowledged", "you")   # no session here: "you"
    assert DATA["incidents"][0]["state"] == "open"                                  # the cached object is not changed
    at.run()
    assert calls.renders[-1]["data"]["incidents"][0]["state"] == "acknowledged"    # still patched on the next run
    assert at.session_state["icecore_console_pending_acks"][KEY]["user"] == "you"
    # a later build shows it acknowledged: the patch ends
    calls_build = lambda: {**DATA, "incidents": [{"incident_key": KEY, "state": "acknowledged", "acknowledged_by": "jane"}]}
    from lib import console_data
    monkeypatch.setattr(console_data, "build", lambda days=30, layer=None, now=None: calls_build())
    import streamlit as st
    st.cache_data.clear()
    at.run()
    assert calls.renders[-1]["data"]["incidents"][0]["acknowledged_by"] == "jane"
    assert at.session_state["icecore_console_pending_acks"] == {}


def test_an_ack_the_views_never_show_stops_being_patched_after_the_time_limit(monkeypatch):
    import time as _time
    at, calls = run(monkeypatch, actions=[{"action": "ack", "incident_key": KEY, "nonce": "1"}])
    assert calls.renders[-1]["data"]["incidents"][0]["state"] == "acknowledged"
    started = _time.time()
    monkeypatch.setattr(_time, "time", lambda: started + 121)   # past PENDING_ACK_TTL_S; the views still say open
    at.run()
    assert calls.renders[-1]["data"]["incidents"][0]["state"] == "open"
    assert at.session_state["icecore_console_pending_acks"] == {}


def test_the_host_reads_the_user_through_the_query_layer():
    text = Path(APP).read_text()
    assert "queries.current_user()" in text and "session.sql" not in text
