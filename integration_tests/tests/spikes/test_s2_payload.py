"""Spike S2: payload limits, framed on the Gate 1 write path (option B).

Gate 1 chose two persisted procedures: ICECORE_EMIT (SQL, SYSTEM$ADD_EVENT per event, SCOPE global)
and ICECORE_EMIT_RUN (Python, custom spans with explicit times, SCOPE icecore). This module measures:

- one record per CALL whose message or `icecore.payload` attribute is size_kb x 1024 ASCII characters,
  for the S1 temporary-procedure probes (sql_log, sql_event, python_log, python_attr) and for the
  ICECORE_EMIT probe (proc_event);
- batches in one CALL: ICECORE_EMIT with n span events, ICECORE_EMIT_RUN with n spans.

Every case asserts the measured behaviour (intact, truncated or dropped; see S2.md), so the sandbox
suite stays green; absence is shown with assert_no_events over 180 s.
"""
import json
import time
from pathlib import Path

import pytest
from dbt.cli.main import dbtRunner

from helpers.events import assert_no_events, wait_for_events
from helpers.findings import record_finding

IT_DIR = Path(__file__).resolve().parents[2]

MECHANISMS = ["sql_log", "sql_event", "python_log", "python_attr", "proc_event"]
SIZES_KB = [8, 64, 256, 1024]
THRESHOLD_KB = [384, 512, 768, 960]
TIMEOUT_S = 180
ABSENT_WAIT_S = 180
WHERE = (
    "timestamp > dateadd(hour, -6, current_timestamp()) and "
    "(record_attributes:\"icecore.run_id\" = %(r)s or value::string ilike %(p)s)"
)


PY_MESSAGE_LIMIT = 524_252  # measured: Python log messages are cut here and suffixed with "..."
EVENTS_PER_CALL = 128       # measured: a CALL keeps only its last 128 span events (span: dropped_events_count)
BYTES_PER_CALL_KB = 960     # measured: kept events totalling 15 x 64 KB land, 16 x 64 KB land none


def _expected(mechanism: str, size_kb: int) -> str:
    """Measured behaviour for one record of size_kb (see S2.md)."""
    if mechanism == "python_log" and size_kb * 1024 > PY_MESSAGE_LIMIT:
        return "truncated"
    return "dropped" if size_kb >= 1024 else "intact"


def _outcome(hits, finding) -> str:
    if not hits:
        return "dropped"
    if finding["intact"]:
        return "intact"
    return "truncated" if finding["payload_chars"] == PY_MESSAGE_LIMIT else "corrupted"


def _proc_span(sf, run_id: str, hits) -> dict:
    """The probe procedure's own SPAN record: did it land, and did Snowflake report dropped events?"""
    spans = wait_for_events(
        sf, "timestamp > dateadd(hour, -6, current_timestamp()) and record_type = 'SPAN' "
            "and resource_attributes:\"snow.executable.name\"::string ilike %(e)s",
        {"e": f"ICECORE_EMIT_{run_id.upper()}(%"}, min_rows=0, timeout_s=0)
    seqs = sorted((h["record_attributes"] or {}).get("icecore.seq", 0) for h in hits)
    return {"span_landed": len(spans), "dropped_events_count": (spans[0]["record"] or {}).get("dropped_events_count")
            if spans else None, "seq_range": [seqs[0], seqs[-1]] if seqs else None}


def _payload_chars(value) -> int:
    """Leading 'x' characters: the payload part of what landed."""
    if not isinstance(value, str):
        return 0
    return len(value) - len(value.lstrip("x"))


def _assert_absent(sf, run_id: str, key: str) -> None:
    """Measured: nothing tagged with run_id lands (the record was dropped)."""
    try:
        assert_no_events(sf, WHERE, {"r": run_id, "p": f"%{run_id}%"}, wait_s=ABSENT_WAIT_S)
    except AssertionError:
        record_finding("s2", f"{key}.absent_after_s", None)
        raise
    record_finding("s2", f"{key}.absent_after_s", ABSENT_WAIT_S)


TRUNCATION_FREE_KEYS = {"code.filepath", "code.function", "code.lineno", "icecore.payload_kb", "icecore.run_id"}


def _assert_truncated(finding) -> None:
    """Measured for Python log messages: cut to 524,252 characters plus '...', with no flag attribute."""
    assert finding["landed_rows"] == 1, finding
    assert finding["landed_len"] == PY_MESSAGE_LIMIT + 3 and finding["payload_chars"] == PY_MESSAGE_LIMIT, finding
    assert finding["tail"].endswith("x..."), finding
    assert set(finding["record_attribute_keys"]) <= TRUNCATION_FREE_KEYS, finding


def _run_case(sf, run_id, mechanism, size_kb, key, macro="s2_payload", args=None, expected_rows=1, wait=True):
    args = args or {"run_id": run_id, "mechanism": mechanism, "size_kb": size_kb}
    errors: list[str] = []  # dbtRunnerResult has no message for a failed run-operation; the events do
    warnings: list[str] = []  # the procedure path turns a failed CALL into an icecore warning

    def keep_errors(ev):
        if ev.info.level == "error" and ev.info.msg:
            errors.append(str(ev.info.msg))
        elif ev.info.level == "warn" and ev.info.msg and "icecore" in str(ev.info.msg):
            warnings.append(str(ev.info.msg)[:500])

    t0 = time.monotonic()
    res = dbtRunner(callbacks=[keep_errors]).invoke(
        ["run-operation", macro, "--args", json.dumps(args), "--project-dir", str(IT_DIR),
         "--profiles-dir", str(IT_DIR)])
    wall_s = round(time.monotonic() - t0, 2)

    rows, visible_s = [], None
    if res.success and wait:
        t1 = time.monotonic()
        try:
            rows = wait_for_events(sf, WHERE, {"r": run_id, "p": f"%{run_id}%"}, min_rows=expected_rows,
                                   timeout_s=TIMEOUT_S)
            visible_s = round(time.monotonic() - t1, 1)
        except TimeoutError:  # keep whatever landed: a partial batch is a measurement too
            rows = wait_for_events(sf, WHERE, {"r": run_id, "p": f"%{run_id}%"}, min_rows=0, timeout_s=0)

    want_type = "SPAN_EVENT" if mechanism in ("sql_event", "proc_event") else "LOG"
    hits = [r for r in rows if r["record_type"] == want_type]
    if mechanism.startswith("python"):
        hits = [r for r in hits if ((r["scope"] or {}).get("name") == "icecore")]
    landed = None
    if hits:
        r = hits[0]
        in_attr = mechanism in ("sql_event", "python_attr", "proc_event")
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
        "tail": landed[-12:] if isinstance(landed, str) else None,
        "record_attribute_keys": sorted((hits[0]["record_attributes"] or {}).keys()) if hits else [],
        "icecore_warnings": warnings,
    }
    if mechanism == "proc_event":
        finding.update(_proc_span(sf, run_id, hits))
    if not res.success:
        finding["error"] = " | ".join(errors)[:800] or "run-operation failed"
    record_finding("s2", key, finding)
    return res, hits, finding


def _check_single(sf, run_id, mechanism, size_kb, key):
    expected = _expected(mechanism, size_kb)
    res, hits, finding = _run_case(sf, run_id, mechanism, size_kb, key, wait=expected != "dropped")
    record_finding("s2", f"{key}.expected", expected)
    assert res.success, finding  # a hook must never fail on size
    assert not finding["icecore_warnings"], finding
    if expected == "dropped":
        _assert_absent(sf, run_id, key)
    elif expected == "truncated":
        _assert_truncated(finding)
    else:
        assert finding["landed_rows"] == 1 and finding["intact"], finding


@pytest.mark.sandbox
@pytest.mark.parametrize("mechanism", MECHANISMS)
@pytest.mark.parametrize("size_kb", SIZES_KB)
def test_s2_payload(dbt, sf, run_id, mechanism, size_kb):
    _check_single(sf, run_id, mechanism, size_kb, f"{mechanism}.{size_kb}")


@pytest.mark.sandbox
@pytest.mark.parametrize("mechanism", MECHANISMS)
@pytest.mark.parametrize("size_kb", THRESHOLD_KB)
def test_s2_payload_threshold(dbt, sf, run_id, mechanism, size_kb):
    """Between 256 KB (all intact) and 1024 KB (dropped or truncated): where the limit sits."""
    _check_single(sf, run_id, mechanism, size_kb, f"threshold.{mechanism}.{size_kb}")


BATCHES = [(8, 64), (12, 64), (15, 64), (16, 64), (32, 64), (64, 64),  # total size: 0.5 to 4 MB
           (128, 1), (129, 1), (256, 1), (1000, 1)]  # event count at 1 KB each


@pytest.mark.sandbox
@pytest.mark.parametrize("n_events,event_kb", BATCHES)
def test_s2_proc_batch_size(dbt, sf, run_id, n_events, event_kb):
    """ICECORE_EMIT batches span events in one CALL. Which batches land (total size, event count)?"""
    key = f"proc_batch.{n_events}x{event_kb}"
    # Measured: a CALL keeps its LAST 128 events and the procedure's span reports dropped_events_count;
    # if the kept events total about 1 MB or more, none land and the span reports nothing.
    kept = min(n_events, EVENTS_PER_CALL)
    expected = kept if kept * event_kb <= BYTES_PER_CALL_KB else 0
    res, hits, finding = _run_case(sf, run_id, "proc_event", event_kb, key, macro="s2_proc_batch",
                                   args={"run_id": run_id, "n_events": n_events, "event_kb": event_kb},
                                   expected_rows=n_events, wait=expected > 0)
    record_finding("s2", f"{key}.expected_landed", expected)
    assert res.success and not finding["icecore_warnings"], finding
    if expected == 0:
        _assert_absent(sf, run_id, key)
        record_finding("s2", f"{key}.span", _proc_span(sf, run_id, []))
        return
    assert len(hits) == expected, finding
    if n_events > EVENTS_PER_CALL:
        assert finding["dropped_events_count"] == n_events - EVENTS_PER_CALL, finding
        assert finding["seq_range"] == [n_events - EVENTS_PER_CALL + 1, n_events], finding


RUN_BATCHES = [(100, 1), (200, 1), (15, 64), (16, 64), (64, 64)]


@pytest.mark.sandbox
@pytest.mark.parametrize("n_spans,span_kb", RUN_BATCHES)
def test_s2_emit_run_batch_size(dbt, sf, run_id, n_spans, span_kb):
    """ICECORE_EMIT_RUN writes a whole run's spans in one CALL. Which batches land?"""
    key = f"emit_run_batch.{n_spans}x{span_kb}"
    start_ns = (int(time.time()) - 3600) * 1_000_000_000
    errors: list[str] = []
    warnings: list[str] = []

    def keep(ev):
        if ev.info.level == "error" and ev.info.msg:
            errors.append(str(ev.info.msg))
        elif ev.info.level == "warn" and ev.info.msg and "icecore" in str(ev.info.msg):
            warnings.append(str(ev.info.msg)[:500])

    t0 = time.monotonic()
    res = dbtRunner(callbacks=[keep]).invoke(
        ["run-operation", "s2_emit_run_batch", "--args",
         json.dumps({"run_id": run_id, "n_spans": n_spans, "span_kb": span_kb, "start_ns": start_ns}),
         "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])
    wall_s = round(time.monotonic() - t0, 2)
    rows = []
    if res.success:
        try:
            rows = wait_for_events(sf, WHERE, {"r": run_id, "p": f"%{run_id}%"}, min_rows=n_spans, timeout_s=TIMEOUT_S)
        except TimeoutError:
            rows = wait_for_events(sf, WHERE, {"r": run_id, "p": f"%{run_id}%"}, min_rows=0, timeout_s=0)
    spans = [r for r in rows if r["record_type"] == "SPAN"]
    intact = sum(1 for r in spans if len((r["record_attributes"] or {}).get("icecore.payload", "")) == span_kb * 1024)
    finding = {"run_id": run_id, "call_ok": res.success, "dbt_wall_s": wall_s, "expected": n_spans,
               "landed_spans": len(spans), "intact_payloads": intact, "icecore_warnings": warnings,
               "errors": errors[:2], "total_kb": n_spans * span_kb}
    record_finding("s2", key, finding)
    assert res.success and not warnings, finding
    # Measured: every span is its own record, so neither the 128-event nor the ~1 MB limit of ICECORE_EMIT
    # applies to the CALL; 200 spans and 4 MB in one CALL all land intact.
    assert len(spans) == n_spans and intact == n_spans, finding
