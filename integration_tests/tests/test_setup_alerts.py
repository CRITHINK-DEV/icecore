"""UC5 setup creates the alerts and the silence task (macros/setup/setup.sql, create_tasks.sql, render_alert_sql.sql).

With var alerts true, setup creates one ALERT per channel and the silence TASK and resumes them; a second setup
leaves exactly the same objects (no duplicates); with alerts false it creates neither. Offline resume-rule tests are
in test_render_alert.py. These sandbox tests use a run_id-named obs schema, resume only inside it, and suspend and
drop everything at the end (a resumed alert or task costs warehouse time, so the window is a few minutes).
"""
import json

import pytest
from snowflake.connector import DictCursor

from helpers.macros import run_macro_with_logs
from test_setup import DATABASE, fresh_schema  # noqa: F401  (fixture)

# the fixture models of the test project route to the channels email and chat: they must exist
CHANNELS = {
    "email": {"type": "email", "integration": "ICECORE_NO_SUCH_INTEGRATION", "recipients": ["data@example.com"], "default": True},
    "chat": {"type": "slack", "integration": "ICECORE_NO_SUCH_INTEGRATION"}}


def _setup(schema, settings):
    extra = ["--vars", json.dumps({"icecore": settings})]
    return run_macro_with_logs("it_call", {"name": "setup", "kwargs": {"schema": schema}}, extra)


def _show(sf, kind, schema):
    return sf.cursor(DictCursor).execute(f'show {kind} in schema "{DATABASE}"."{schema}"').fetchall()


def _cleanup(sf, schema):
    cur = sf.cursor()
    for kind, show in (("alert", "alerts"), ("task", "tasks")):
        for row in _show(sf, show, schema):
            cur.execute(f'alter {kind} if exists "{DATABASE}"."{schema}"."{row["name"]}" suspend')


@pytest.mark.sandbox
def test_alerts_true_creates_the_alerts_and_the_silence_task_resumed_and_a_second_setup_adds_none(sf, fresh_schema):
    schema = fresh_schema
    settings = {"alerts": True, "alert_channels": CHANNELS}
    try:
        first, logs = _setup(schema, settings)
        alerts = _show(sf, "alerts", schema)
        tasks = {t["name"]: t["state"] for t in _show(sf, "tasks", schema)}
        assert [(a["name"], a["state"]) for a in alerts] == [("ICECORE_ALERT_CHAT", "started"), ("ICECORE_ALERT_EMAIL", "started")], alerts
        assert tasks["ICECORE_SILENCE"] == "started", tasks
        assert [(a["channel"], a["status"], a["resumed"]) for a in first["alerts"]] == [("chat", "ok", True), ("email", "ok", True)]
        silence = [t for t in first["tasks"]["tasks"] if t["name"] == "silence"][0]
        assert silence["status"] == "ok" and silence["resumed"] is True
        assert any("warehouse time" in line for line in logs), logs

        second, _ = _setup(schema, settings)
        assert [(a["name"], a["state"]) for a in _show(sf, "alerts", schema)] == [("ICECORE_ALERT_CHAT", "started"), ("ICECORE_ALERT_EMAIL", "started")]
        assert {t["name"]: t["state"] for t in _show(sf, "tasks", schema)} == tasks
        assert [(a["channel"], a["resumed"]) for a in second["alerts"]] == [("chat", True), ("email", True)]
    finally:
        _cleanup(sf, schema)


@pytest.mark.sandbox
def test_a_resume_var_set_to_false_keeps_that_object_suspended(sf, fresh_schema):
    schema = fresh_schema
    try:
        out, _ = _setup(schema, {"alerts": True, "alert_channels": CHANNELS, "alert_resume": False, "silence_task_resume": False})
        assert {a["state"] for a in _show(sf, "alerts", schema)} == {"suspended"}
        assert {t["state"] for t in _show(sf, "tasks", schema) if t["name"] == "ICECORE_SILENCE"} == {"suspended"}
        assert [a["resumed"] for a in out["alerts"]] == [False, False]
    finally:
        _cleanup(sf, schema)


@pytest.mark.sandbox
def test_alerts_false_creates_no_alert_and_no_silence_task(sf, fresh_schema):
    schema = fresh_schema
    try:
        out, logs = _setup(schema, {"alerts": False, "alert_channels": CHANNELS})
        assert _show(sf, "alerts", schema) == []
        assert "ICECORE_SILENCE" not in {t["name"] for t in _show(sf, "tasks", schema)}
        assert out["alerts"] == []
        step = [s for s in out["steps"] if s["name"] == "create_alerts"][0]
        assert step["status"] == "done" and "alerts is false" in step["detail"]
    finally:
        _cleanup(sf, schema)


@pytest.mark.sandbox
def test_alert_resume_true_creates_and_resumes_the_alerts_even_with_alerts_false(sf, fresh_schema):
    schema = fresh_schema
    try:
        _setup(schema, {"alerts": False, "alert_resume": True, "alert_channels": CHANNELS})
        assert {a["state"] for a in _show(sf, "alerts", schema)} == {"started"}
        assert "ICECORE_SILENCE" not in {t["name"] for t in _show(sf, "tasks", schema)}
    finally:
        _cleanup(sf, schema)


@pytest.mark.sandbox
def test_silence_task_resume_true_creates_and_resumes_the_task_even_with_alerts_false(sf, fresh_schema):
    schema = fresh_schema
    try:
        _setup(schema, {"alerts": False, "silence_task_resume": True, "alert_channels": CHANNELS})
        assert _show(sf, "alerts", schema) == []
        assert {t["name"]: t["state"] for t in _show(sf, "tasks", schema)}["ICECORE_SILENCE"] == "started"
    finally:
        _cleanup(sf, schema)


@pytest.mark.sandbox
def test_a_running_task_whose_var_now_says_false_is_recreated_suspended(sf, fresh_schema):
    schema = fresh_schema
    try:
        _setup(schema, {"alerts": True, "alert_channels": CHANNELS})
        assert {t["name"]: t["state"] for t in _show(sf, "tasks", schema)}["ICECORE_SILENCE"] == "started"
        _setup(schema, {"alerts": True, "alert_channels": CHANNELS, "silence_task_resume": False, "alert_resume": False})
        assert {t["name"]: t["state"] for t in _show(sf, "tasks", schema)}["ICECORE_SILENCE"] == "suspended"
        assert {a["state"] for a in _show(sf, "alerts", schema)} == {"suspended"}
    finally:
        _cleanup(sf, schema)


@pytest.mark.sandbox
def test_alerts_false_after_an_earlier_setup_warns_per_object_and_touches_nothing(sf, fresh_schema):
    schema = fresh_schema
    try:
        _setup(schema, {"alerts": True, "alert_channels": CHANNELS})
        before = ({(a["name"], a["state"], a["created_on"]) for a in _show(sf, "alerts", schema)},
                  {(t["name"], t["state"], t["created_on"]) for t in _show(sf, "tasks", schema) if t["name"] == "ICECORE_SILENCE"})
        _, logs = _setup(schema, {"alerts": False, "alert_channels": CHANNELS})
        text = "\n".join(logs)
        for name in ("ICECORE_SILENCE", "ICECORE_ALERT_CHAT", "ICECORE_ALERT_EMAIL"):
            assert f"{name} exists and is started" in text, text
        assert text.count(" SUSPEND;") == 3 and text.count("DROP ") >= 3
        after = ({(a["name"], a["state"], a["created_on"]) for a in _show(sf, "alerts", schema)},
                 {(t["name"], t["state"], t["created_on"]) for t in _show(sf, "tasks", schema) if t["name"] == "ICECORE_SILENCE"})
        assert after == before
    finally:
        _cleanup(sf, schema)
