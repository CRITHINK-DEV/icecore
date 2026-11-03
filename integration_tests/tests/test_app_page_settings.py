"""Settings & about: data layer (lib/config_logic.py), page logic and render, offline."""
import sys

import pytest
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import config_logic as cl  # noqa: E402

T0 = datetime(2026, 11, 1, 8, 0, 0)
CAP = {"database_name": "DB", "detected_at": T0, "edition": "ENTERPRISE", "edition_source": "show_parameters",
       "event_table": "DB.TELEMETRY.EVENTS", "event_table_level": "account", "cortex_available": True,
       "cortex_reason": None, "log_level": "INFO", "trace_level": "ALWAYS", "icecore_version": "0.4.0"}


def test_capability_rows_show_edition_event_table_and_cortex():
    d = dict(cl.capability_rows(CAP))
    assert d["Edition"].startswith("ENTERPRISE") and d["Event table"].startswith("DB.TELEMETRY.EVENTS")
    assert d["Cortex"] == "available" and d["icecore version (setup)"] == "0.4.0"
    off = dict(cl.capability_rows({**CAP, "cortex_available": False, "cortex_reason": "region"}))
    assert off["Cortex"] == "not available: region"
    unknown = dict(cl.capability_rows({"edition": None, "cortex_available": None}))
    assert unknown["Edition"] == "unknown" and unknown["Cortex"] == "unknown"


def test_run_versions_show_dbt_and_say_when_icecore_is_not_recorded():
    runs = [{"invocation_id": "i1", "project": "p", "target": "prod", "started_at": T0, "dbt_version": "1.8.10"},
            {"invocation_id": "i2", "project": "p", "target": "prod", "started_at": T0, "dbt_version": None}]
    rows = cl.run_versions(runs)
    assert rows[0]["dbt"] == "1.8.10" and rows[1]["dbt"] == "unknown"
    assert rows[0]["icecore"] == cl.NOT_RECORDED
    assert "INVOCATIONS" in cl.ICECORE_VERSION_NOTE


def test_run_versions_use_icecore_version_when_the_view_carries_it():
    rows = cl.run_versions([{"invocation_id": "i1", "icecore_version": "0.4.0", "dbt_version": "1.9.0"}])
    assert rows[0]["icecore"] == "0.4.0"


def test_channels_from_declared_and_silences_with_last_notification_unknown():
    declared = [{"unique_id": "a", "channel": "data"}, {"unique_id": "b", "channel": "data"},
                {"unique_id": "c", "channel": None}]
    silences = [{"channel": "data", "detected_at": T0}, {"channel": "ops", "detected_at": T0.replace(hour=9)}]
    rows = cl.channel_rows(declared, silences)
    assert [r["channel"] for r in rows] == ["data", "ops"]
    assert rows[0]["models"] == 2 and rows[1]["models"] == 0
    assert rows[0]["last_notified_at"] == cl.NOT_RECORDED
    assert rows[1]["last_silence_at"] == T0.replace(hour=9)


def test_channels_use_notification_records_when_given():
    rows = cl.channel_rows([], [], notified=[{"channel": "slack", "notified_at": T0}])
    assert rows == [{"channel": "slack", "models": 0, "last_silence_at": None, "last_notified_at": T0}]
    assert cl.channel_rows([], []) == []


# --- page logic (lib/pages/settings.py) and the render ----------------------------------------------------------


@pytest.fixture
def logic():
    pytest.importorskip("streamlit")  # lib.pages.settings imports lib.console; the dbt venvs have no streamlit
    from lib.pages import settings
    return settings


def test_settings_tiles(logic):
    t = logic.tiles(CAP, [{"channel": "data"}])
    assert [x.label for x in t] == ["Edition", "icecore version", "Cortex anomaly engine", "Alert channels"]
    assert [x.value for x in t] == ["ENTERPRISE", "0.4.0", "Available", 1]


def test_settings_all_empty(logic):
    assert logic.latest_capabilities([]) == {}
    t = logic.tiles({}, [])
    assert [x.value for x in t] == ["—", "—", "—", 0]
    assert logic.version_rows([]) == [] and logic.channel_rows([], []) == []


def test_settings_without_new_columns(logic):
    """Older views: no icecore_version on INVOCATIONS, no NOTIFICATIONS view, no icecore_version in CAPABILITIES."""
    runs = [{"invocation_id": "i1", "dbt_version": "1.8.10", "started_at": T0}]
    assert logic.version_rows(runs)[0]["icecore"] == "not recorded yet"
    ch = logic.channel_rows([{"channel": "data"}], [])
    assert ch[0]["last_notified_at"] == "not recorded yet"
    assert dict(logic.capability_rows({"edition": "STANDARD"}))["icecore version (setup)"] == "unknown"


def test_settings_version_rows_newest_first_and_limited(logic):
    runs = [{"invocation_id": str(i), "started_at": T0.replace(day=i)} for i in range(1, 6)] + [{"invocation_id": "n"}]
    rows = logic.version_rows(runs, limit=3)
    assert [r["invocation_id"] for r in rows] == ["5", "4", "3"]


def test_settings_latest_capabilities_picks_newest(logic):
    rows = [{"detected_at": T0, "edition": "A"}, {"detected_at": T0.replace(day=2), "edition": "B"}]
    assert logic.latest_capabilities(rows)["edition"] == "B"


def test_settings_read_error_shows_caption():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP_DIR / "pages" / "settings.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Settings & about" and at.info
    assert any("Reading the views failed" in c.value for c in at.caption)


def test_settings_page_renders_with_reads_patched(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from lib import queries
    monkeypatch.setattr(queries, "capabilities", lambda limit=1, layer=None: [CAP])
    monkeypatch.setattr(queries, "invocations", lambda *a, **k: [
        {"invocation_id": "i1", "project": "p", "target": "prod", "started_at": T0, "dbt_version": "1.8.10"}])
    monkeypatch.setattr(queries, "sla_declared", lambda *a, **k: [{"unique_id": "a", "channel": "data"}])
    monkeypatch.setattr(queries, "silence_events", lambda *a, **k: [])
    at = AppTest.from_file(str(APP_DIR / "pages" / "settings.py"), default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert [m.label for m in at.metric] == ["Edition", "icecore version", "Cortex anomaly engine", "Alert channels"]
    assert [s.value for s in at.subheader] == ["Capabilities", "Alert channels", "Versions per run"]
    assert not at.info
