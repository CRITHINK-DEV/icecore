"""Settings page logic (streamlit/lib/config_logic.py), offline. The page itself is paused (UI design pending)."""
import sys
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
