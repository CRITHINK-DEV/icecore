"""Spike S2: payload limits per write mechanism.

One record per case whose message (logs) or attribute `icecore.payload` (span event, Python
log attribute) is size_kb x 1024 ASCII characters, emitted through `s2_payload`, i.e. the S1
probes run as a temporary procedure (S1: anonymous statements never reach the database's event
table). `python_attr` is added to the plan's three mechanisms because the S1 recommendation (E2)
carries node results as LOG attributes. Records whether the call succeeded, whether a row landed
and how much of the payload arrived.
"""
import json
import time
from pathlib import Path

import pytest
from dbt.cli.main import dbtRunner

from helpers.events import wait_for_events
from helpers.findings import record_finding

IT_DIR = Path(__file__).resolve().parents[2]

MECHANISMS = ["sql_log", "sql_event", "python_log", "python_attr"]
SIZES_KB = [8, 64, 256, 1024]
THRESHOLD_KB = [384, 512, 768, 960]
TIMEOUT_S = 180
WHERE = (
    "timestamp > dateadd(hour, -6, current_timestamp()) and "
    "(record_attributes:\"icecore.run_id\" = %(r)s or value::string ilike %(p)s)"
)


def _payload_chars(value) -> int:
    """Leading 'x' characters: the payload part of what landed."""
    if not isinstance(value, str):
        return 0
    return len(value) - len(value.lstrip("x"))


def _run_case(sf, run_id, mechanism, size_kb, key):
    args = {"run_id": run_id, "mechanism": mechanism, "size_kb": size_kb}
    errors: list[str] = []  # dbtRunnerResult has no message for a failed run-operation; the events do

    def keep_errors(ev):
        if ev.info.level == "error" and ev.info.msg:
            errors.append(str(ev.info.msg))

    t0 = time.monotonic()
    res = dbtRunner(callbacks=[keep_errors]).invoke(
        ["run-operation", "s2_payload", "--args", json.dumps(args), "--project-dir", str(IT_DIR),
         "--profiles-dir", str(IT_DIR)])
    wall_s = round(time.monotonic() - t0, 2)

    rows, visible_s = [], None
    if res.success:
        t1 = time.monotonic()
        try:
            rows = wait_for_events(sf, WHERE, {"r": run_id, "p": f"%{run_id}%"}, min_rows=1, timeout_s=TIMEOUT_S)
            visible_s = round(time.monotonic() - t1, 1)
        except TimeoutError:
            rows = []

    want_type = "SPAN_EVENT" if mechanism == "sql_event" else "LOG"
    hits = [r for r in rows if r["record_type"] == want_type]
    if mechanism.startswith("python"):
        hits = [r for r in hits if ((r["scope"] or {}).get("name") == "icecore")]
    landed = None
    if hits:
        r = hits[0]
        in_attr = mechanism in ("sql_event", "python_attr")
        landed = (r["record_attributes"] or {}).get("icecore.payload") if in_attr else r["value"]
    expected = size_kb * 1024
    finding = {
        "run_id": run_id,
        "call_ok": res.success,
        "dbt_wall_s": wall_s,
        "landed_rows": len(hits),
        "seconds_to_visible": visible_s,
        "expected_chars": expected,
        "landed_len": len(landed) if isinstance(landed, str) else None,
        "payload_chars": _payload_chars(landed) if hits else None,
        "intact": _payload_chars(landed) == expected if hits else None,
        "record_attribute_keys": sorted((hits[0]["record_attributes"] or {}).keys()) if hits else [],
    }
    if not res.success:
        finding["error"] = " | ".join(errors)[:800] or "run-operation failed"
    record_finding("s2", key, finding)
    return res, hits, finding


@pytest.mark.sandbox
@pytest.mark.parametrize("mechanism", MECHANISMS)
@pytest.mark.parametrize("size_kb", SIZES_KB)
def test_s2_payload(dbt, sf, run_id, mechanism, size_kb):
    res, hits, finding = _run_case(sf, run_id, mechanism, size_kb, f"{mechanism}.{size_kb}")
    assert res.success  # a hook must never fail on size
    assert hits, finding  # a silent drop is a finding


@pytest.mark.sandbox
@pytest.mark.parametrize("mechanism", MECHANISMS)
@pytest.mark.parametrize("size_kb", THRESHOLD_KB)
def test_s2_payload_threshold(dbt, sf, run_id, mechanism, size_kb):
    """Between 256 KB (all intact) and 1024 KB (dropped or truncated): where the limit sits."""
    res, hits, finding = _run_case(sf, run_id, mechanism, size_kb, f"threshold.{mechanism}.{size_kb}")
    assert res.success
