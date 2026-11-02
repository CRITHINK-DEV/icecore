"""SLAs page logic (streamlit/lib/slas.py), offline."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import slas  # noqa: E402

T0 = datetime(2026, 11, 1, 8, 0, 0)


def silence(uid="model.p.orders", detected=T0, every=60, grace=5):
    return {"unique_id": uid, "expected_at": T0 - timedelta(hours=1), "grace": grace,
            "expected_every_minutes": every, "last_success_at": T0 - timedelta(hours=3),
            "detected_at": detected, "channel": "data", "severity": "warn"}


def run(uid, minutes, status="success"):
    return {"unique_id": uid, "status": status, "started_at": T0 + timedelta(minutes=minutes)}


def test_declared_and_actual_schedules_are_listed():
    rows = slas.sla_schedule_table([silence()], [run("model.p.orders", m) for m in (0, 120, 240)]
                                   + [run("model.p.orders", 300, "error"), run("model.p.other", 0)])
    assert len(rows) == 1
    r = rows[0]
    assert (r["declared_every"], r["grace"], r["actual_every"]) == ("1h", "5m", "2h")
    assert r["runs_observed"] == 3 and r["slower_than_declared"] is True
    assert r["last_success_at"] == T0 + timedelta(minutes=240)


def test_latest_verdict_wins_and_no_runs_is_handled():
    d = slas.declared_schedules([silence(detected=T0, every=60), silence(detected=T0 + timedelta(hours=1), every=30)])
    assert d["model.p.orders"]["expected_every_minutes"] == 30
    row = slas.sla_schedule_table([silence()], [])[0]
    assert row["actual_every"] == "" and row["runs_observed"] == 0 and row["slower_than_declared"] is False


def test_silence_events_show_expected_and_last_success():
    rows = slas.silence_table([silence(detected=T0), silence("model.p.b", detected=T0 + timedelta(hours=1))])
    assert [r["unique_id"] for r in rows] == ["model.p.b", "model.p.orders"]
    assert rows[0]["expected_at"] and rows[0]["last_success_at"]


def test_empty_state_has_the_meta_sla_example():
    assert "sla:" in slas.SLA_EXAMPLE and "expected_every" in slas.SLA_EXAMPLE and "grace" in slas.SLA_EXAMPLE
    assert "meta.sla" in slas.EMPTY_MESSAGE
    assert slas.sla_schedule_table([], []) == [] and slas.silence_table([]) == []


def test_format_minutes():
    assert [slas.format_minutes(m) for m in (None, 1, 90, 1440)] == ["", "1m", "1h30m", "1d"]


def test_page_wires_empty_state_and_example():
    src = (APP_DIR / "pages" / "slas.py").read_text()
    assert "empty_state(slas.EMPTY_MESSAGE)" in src and "SLA_EXAMPLE" in src
