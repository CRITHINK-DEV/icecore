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


def decl(uid="model.p.orders", every=60, grace=5):
    return {"unique_id": uid, "expected_every_minutes": every, "grace_minutes": grace, "channel": "data",
            "severity": "warn"}


def run(uid, minutes, status="success"):
    return {"unique_id": uid, "status": status, "started_at": T0 + timedelta(minutes=minutes)}


def test_declared_and_actual_schedules_are_listed_for_a_model_that_was_never_silent():
    rows = slas.sla_schedule_table([decl(), decl("model.p.quiet")], [],
                                   [run("model.p.orders", m) for m in (0, 120, 240)]
                                   + [run("model.p.orders", 300, "error"), run("model.p.other", 0)])
    assert [r["unique_id"] for r in rows] == ["model.p.orders", "model.p.quiet"]
    r = rows[0]
    assert (r["declared_every"], r["grace"], r["actual_every"]) == ("1h", "5m", "2h")
    assert r["runs_observed"] == 3 and r["slower_than_declared"] is True and r["silent_now"] is False
    assert r["last_success_at"] == T0 + timedelta(minutes=240)
    assert rows[1]["runs_observed"] == 0 and rows[1]["actual_every"] == "" and rows[1]["silent_now"] is False


def test_silent_now_only_when_no_success_since_the_verdict():
    verdict = silence(detected=T0 + timedelta(hours=5))  # last_success_at = T0 - 3h
    stale = slas.sla_schedule_table([decl()], [verdict], [run("model.p.orders", -240)])
    assert stale[0]["silent_now"] is True
    recovered = slas.sla_schedule_table([decl()], [verdict], [run("model.p.orders", 400)])
    assert recovered[0]["silent_now"] is False


def test_silence_events_show_expected_and_last_success():
    rows = slas.silence_table([silence(detected=T0), silence("model.p.b", detected=T0 + timedelta(hours=1))])
    assert [r["unique_id"] for r in rows] == ["model.p.b", "model.p.orders"]
    assert rows[0]["expected_at"] and rows[0]["last_success_at"]


def test_empty_state_has_the_meta_sla_example():
    assert "sla:" in slas.SLA_EXAMPLE and "expected_every" in slas.SLA_EXAMPLE and "grace" in slas.SLA_EXAMPLE
    assert "meta.sla" in slas.EMPTY_MESSAGE
    assert slas.sla_schedule_table([], [], []) == [] and slas.silence_table([]) == []
    assert "1m, capped at expected_every" in slas.SLA_EXAMPLE and "m, h, d, w" in slas.SLA_EXAMPLE


def test_format_minutes():
    assert [slas.format_minutes(m) for m in (None, 1, 90, 1440)] == ["", "1m", "1h30m", "1d"]
