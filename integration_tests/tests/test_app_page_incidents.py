"""Incidents page: grouping, tiles, chart rows, the Acknowledge outcomes, and an AppTest render with reads patched.

Run in the Streamlit venv: pytest --noconftest tests/test_app_page_incidents.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import ack  # noqa: E402
from lib.pages import incidents as page  # noqa: E402

NOW = datetime(2026, 11, 3, 12, 0, 0)
K1, K2, K3, K4 = (c * 32 for c in "abcd")


def inc(key, kind="test_failure", severity="error", title="t", first=NOW - timedelta(hours=5), ack_at=None,
        node_key="test.p.t1", times=1):
    return {"incident_key": key, "kind": kind, "node_key": node_key, "severity": severity, "title": title,
            "first_detected_at": first, "last_detected_at": first, "occurrences": times,
            "acknowledged_at": ack_at, "acknowledged_by": "ann" if ack_at else None}


INCIDENTS = [
    inc(K1, title="<b>x</b>"),
    inc(K2, severity="warn", node_key="test.p.t2", title="acked", ack_at=NOW),
    inc(K3, title="old", first=NOW - timedelta(days=9), node_key="test.p.t3"),
    inc(K4, title="fixed", node_key="test.p.t4"),
]
RESULTS = [{"test_unique_id": "test.p.t4", "status": "pass", "executed_at": NOW}]


def classified(incidents=INCIDENTS, results=RESULTS):
    return page.classify(incidents, test_results=results, node_executions=[], metrics=[], now=NOW)


def test_classify_and_group_open_stale_last_acknowledged_resolved():
    states = classified()
    assert [s for _, s in states] == ["open", "acknowledged", "stale", "resolved"]
    groups = page.rows(INCIDENTS, {i["incident_key"]: s for i, s in states}, now=NOW)
    assert [r["incident_key"] for r in groups["open"]] == [K1, K3]
    assert groups["open"][1]["title"].startswith(page.STALE_MARK) and groups["open"][1]["stale"]
    assert not groups["open"][0]["title"].startswith(page.STALE_MARK)
    assert [r["incident_key"] for r in groups["acknowledged"]] == [K2]
    assert [r["incident_key"] for r in groups["resolved"]] == [K4]


def test_row_shape_and_age():
    groups = page.rows(INCIDENTS[:1], {K1: "open"}, now=NOW)
    r = groups["open"][0]
    assert r["kind"] == "Test failure" and r["severity"] == "error" and r["times"] == 1
    assert r["open_for"] == "5h"
    assert r["acknowledged_by"] == "Nobody"


def test_tiles_open_acknowledged_errors_oldest():
    tiles = page.tiles(classified(), now=NOW)
    assert [t.label for t in tiles] == ["Open", "Acknowledged", "Errors", "Oldest open"]
    assert [t.value for t in tiles] == [1, 1, 1, "5h"]
    assert page.tiles([], now=NOW)[3].value is None


def test_per_day_by_kind():
    rows = page.per_day(INCIDENTS)
    assert {"day": NOW.date().isoformat(), "kind": "Test failure", "incidents": 3} in rows
    assert {"day": (NOW - timedelta(days=9)).date().isoformat(), "kind": "Test failure", "incidents": 1} in rows
    assert page.per_day([]) == []


def test_schema_change_resolves_when_acknowledged():
    i = inc(K1, kind="schema_change", node_key="m.a", ack_at=NOW)
    assert classified([i])[0][1] == "resolved"


def test_title_with_markup_stays_text():
    r = page.rows([inc(K1, title="<b>x</b>")], {K1: "open"}, now=NOW)["open"][0]
    assert r["title"] == "<b>x</b>"  # st.dataframe renders cells as text; the page never routes it to markdown or html


def test_without_new_columns():
    bare = {"incident_key": K1, "kind": "test_failure", "title": "t"}
    groups = page.rows([bare], {K1: "open"}, now=NOW)
    assert groups["open"][0]["open_for"] == "—" and groups["open"][0]["times"] is None


@pytest.mark.parametrize("kind,message,icon_has", [
    (ack.OK, "Incident acknowledged. ALERTS_FEED now shows when and by whom.", "✅"),
    (ack.ALREADY, "This incident is already acknowledged.", "ℹ"),
    (ack.REFUSED, "not an open incident", "⚠"),
    (ack.NO_PRIVILEGE, "Your role cannot call ICECORE_ACK", "🚫"),
])
def test_ack_outcomes_map_to_toasts(monkeypatch, kind, message, icon_has):
    monkeypatch.setattr(ack, "acknowledge", lambda key, layer=None: ack.AckResult(kind, message))
    text, icon, ok = page.acknowledge(K1)
    assert text == message and icon_has in icon and ok is (kind == ack.OK)


def test_ack_uses_the_result_message_of_the_real_function():
    class S:
        def __init__(self, status="ok 1", error=None):
            self.status, self.error = status, error

        def sql(self, sql, params=None):
            if sql.startswith("call") and self.error:
                raise self.error
            return self

        def collect(self):
            return [{"ICECORE_ACK": self.status}]

    from lib import queries

    lay = queries.QueryLayer(S(), "DB.ICECORE_OBS")
    assert page.acknowledge(K1, layer=lay)[1].startswith("✅")
    lay = queries.QueryLayer(S(error=Exception("Insufficient privileges")), "DB.ICECORE_OBS")
    assert "cannot call ICECORE_ACK" in page.acknowledge(K1, layer=lay)[0]


def test_incidents_all_empty():
    assert page.classify([], test_results=[], node_executions=[], metrics=[], now=NOW) == []
    assert page.rows([], {}, now=NOW) == {"open": [], "acknowledged": [], "resolved": []}
    assert [t.value for t in page.tiles([], now=NOW)] == [0, 0, 0, None]
    assert page.title_count([]) == "Incidents"


def test_title_count_uses_open_only():
    assert page.title_count(classified()) == "Incidents (1)"


# --- AppTest render -------------------------------------------------------------------------------------------

def render(monkeypatch, feed, error=None):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from lib import queries

    monkeypatch.syspath_prepend(str(APP_DIR))

    def read(*a, **k):
        if error:
            raise error
        return feed

    monkeypatch.setattr(queries, "alerts_feed", read)
    for name in ("test_results", "node_executions", "metrics"):
        monkeypatch.setattr(queries, name, lambda *a, _e=error, **k: (_ for _ in ()).throw(_e) if _e else [])
    at = AppTest.from_file(str(APP_DIR / "pages" / "incidents.py"), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_incidents_render_with_markup_title_as_text(monkeypatch):
    at = render(monkeypatch, INCIDENTS)
    assert at.title[0].value.startswith("Incidents (")
    assert len(at.metric) == 4
    assert at.radio and not at.info
    df = at.dataframe[0].value
    assert "<b>x</b>" in df["title"].tolist() or any("<b>x</b>" in str(t) for t in df["title"])
    assert not any("<b>x</b>" in m.value for m in at.markdown)


def test_incidents_read_error_shows_caption(monkeypatch):
    at = render(monkeypatch, [], error=RuntimeError("no session"))
    assert at.info
    assert any("no session" in c.value for c in at.caption)


def test_incidents_page_all_empty(monkeypatch):
    at = render(monkeypatch, [])
    assert at.info
