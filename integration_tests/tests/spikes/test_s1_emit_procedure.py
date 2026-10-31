"""Spike S1, Gate 1 option B: one persisted SQL procedure that receives an ARRAY of events.

Shape that will ship: `icecore.setup` creates `<target_db>.ICECORE_OBS.ICECORE_EMIT(events ARRAY)`
(LANGUAGE SQL, EXECUTE AS OWNER), and hooks CALL it through `run_query`; each element
`{name, attrs}` becomes one SPAN_EVENT via `SYSTEM$ADD_EVENT`. Here the probe procedure is
`ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_<run_id>`, created by `s1_emit_proc_create` and dropped at the end.

The calls are guarded: `EXECUTE IMMEDIATE $$ ... CALL ... EXCEPTION WHEN OTHER ... $$`, so that a
missing procedure or a missing grant logs a warning instead of failing the dbt run.
"""
import json
import re
import time
from collections import Counter
from pathlib import Path

import pytest
from dbt.cli.main import dbtRunner
from snowflake.connector import DictCursor

from helpers.events import wait_for_events
from helpers.findings import record_finding

IT_DIR = Path(__file__).resolve().parents[2]
ATTRS = {
    "dbt.node.unique_id": "model.p.fct_orders",
    "dbt.result.rows_affected": 1200,
    "dbt.result.execution_time_s": 4.2,
    "dbt.test.failures": 0,
    "icecore.flag": True,
}
WEIRD = "it's a \\ test\nnew line é ü 漢"
DOLLARS = "$$ end of block? $$"
TIMEOUT_S = 180
WHERE = (
    "timestamp > dateadd(hour, -6, current_timestamp()) and "
    "(record_attributes:\"icecore.run_id\" = %(r)s or value::string ilike %(p)s)"
)

pytestmark = pytest.mark.sandbox


def _op(macro: str, args: dict):
    """dbt run-operation; keeps error and warning texts (dbtRunnerResult carries none)."""
    msgs = {"error": [], "warn": [], "info": []}

    def keep(ev):
        if ev.info.level in msgs and ev.info.msg and "icecore" in str(ev.info.msg).lower():
            msgs[ev.info.level].append(str(ev.info.msg))
        elif ev.info.level == "error" and ev.info.msg:
            msgs["error"].append(str(ev.info.msg))

    t0 = time.monotonic()
    res = dbtRunner(callbacks=[keep]).invoke(
        ["run-operation", macro, "--args", json.dumps(args), "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])
    res.msgs = msgs
    return res, round(time.monotonic() - t0, 2)


def _proc(run_id: str) -> str:
    return f"ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_{run_id.upper()}"


@pytest.fixture
def proc(dbt, sf, run_id):
    res, _ = _op("s1_emit_proc_create", {"run_id": run_id})
    assert res.success, res.msgs
    yield _proc(run_id)
    sf.cursor().execute(f"drop procedure if exists {_proc(run_id)}(ARRAY)")


def _wait(sf, tag: str, n: int, timeout_s: int = TIMEOUT_S):
    t0 = time.monotonic()
    params = {"r": tag, "p": f"%{tag}%"}
    try:
        wait_for_events(sf, WHERE, params, min_rows=n, timeout_s=timeout_s)
        visible = round(time.monotonic() - t0, 1)
    except TimeoutError:
        visible = None
    return wait_for_events(sf, WHERE, params, min_rows=0, timeout_s=0), visible


_COMMENT = re.compile(r"^\s*/\*.*?\*/\s*", re.S)


def _server(sf, tag: str) -> dict:
    rows = sf.cursor(DictCursor).execute(
        "select query_type, execution_status, total_elapsed_time, query_text "
        "from table(information_schema.query_history_by_user("
        "end_time_range_start => dateadd(minute, -30, current_timestamp()), result_limit => 10000)) "
        "where query_text ilike %(p)s and query_text not ilike '%%TELEMETRY.EVENTS%%' "
        "and query_text not ilike '%%query_history%%'", {"p": f"%{tag}%"}).fetchall()
    top = [r for r in rows if re.match(r"(EXECUTE IMMEDIATE|CALL)", _COMMENT.sub("", r["QUERY_TEXT"]), re.I)]
    return {"statements": len(top), "server_ms": sum(r["TOTAL_ELAPSED_TIME"] or 0 for r in top),
            "status": dict(Counter(r["EXECUTION_STATUS"] for r in top))}


def _shape(rows) -> dict:
    return {
        "record_types": dict(Counter(r["record_type"] for r in rows)),
        "record_names": sorted({str((r["record"] or {}).get("name")) for r in rows}),
        "scope_names": sorted({str((r["scope"] or {}).get("name")) for r in rows}),
        "snow.database.name": sorted({str((r["resource_attributes"] or {}).get("snow.database.name")) for r in rows}),
        "snow.executable.type": sorted({str((r["resource_attributes"] or {}).get("snow.executable.type")) for r in rows}),
        "snow.executable.name": sorted({str((r["resource_attributes"] or {}).get("snow.executable.name")) for r in rows}),
        "has_snow_query_id": bool(rows) and all((r["resource_attributes"] or {}).get("snow.query.id") for r in rows),
        "trace_ids": len({str((r["trace"] or {}).get("trace_id")) for r in rows}),
    }


def _exact(rows, want: dict) -> int:
    return sum(1 for r in rows if all(k in (r["record_attributes"] or {}) and r["record_attributes"][k] == v
                                      and type(r["record_attributes"][k]) is type(v) for k, v in want.items()))


@pytest.mark.parametrize("mode", ["per_event", "batch", "batch_unguarded"])
def test_s1_proc_per_event_vs_batch(dbt, sf, run_id, proc, mode):
    """100 events: one CALL per event (100 statements) vs one CALL with an ARRAY of 100."""
    macro = "s1_emit_per_event" if mode == "per_event" else "s1_emit_batch"
    res, wall_s = _op(macro, {"run_id": run_id, "proc": proc, "n": 100, "attrs": ATTRS,
                              "guard": mode != "batch_unguarded"})
    rows, visible = _wait(sf, run_id, 100) if res.success else ([], None)
    events = [r for r in rows if r["record_type"] == "SPAN_EVENT"]
    server = _server(sf, run_id)
    finding = {"run_id": run_id, "call_ok": res.success, "landed": len(events), "expected": 100,
               "exact_attributes": _exact(events, {**ATTRS, "icecore.run_id": run_id}),
               "dbt_wall_s": wall_s, "seconds_to_visible": visible, **server, **_shape(events)}
    if not res.success:
        finding["error"] = " | ".join(res.msgs["error"])[:800]
    record_finding("s1", f"proc.{mode}", finding)
    assert res.success
    assert len(events) == 100 and finding["exact_attributes"] == 100, finding


def test_s1_proc_escaping_roundtrip(dbt, sf, run_id, proc):
    attrs = {"icecore.text": WEIRD, "icecore.dollars": DOLLARS}
    res, _ = _op("s1_emit_batch", {"run_id": run_id, "proc": proc, "n": 1, "attrs": attrs, "guard": True})
    rows, _ = _wait(sf, run_id, 1) if res.success else ([], None)
    ok = bool(rows) and _exact(rows, attrs) == len(rows)
    record_finding("s1", "proc.escaping", {"run_id": run_id, "call_ok": res.success, "landed": len(rows),
                                           "byte_identical": ok,
                                           "landed_text": rows[0]["record_attributes"].get("icecore.text") if rows else None})
    assert res.success and ok


@pytest.mark.parametrize("case", ["missing_guarded", "no_usage_guarded", "missing_unguarded"])
def test_s1_proc_missing_or_no_usage_does_not_fail(dbt, sf, run_id, case):
    """A missing procedure, or one in a schema the role has no USAGE on, must not fail the run."""
    target = {"missing_guarded": f"ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_MISSING_{run_id.upper()}",
              "no_usage_guarded": "SNOWFLAKE.TELEMETRY.ICECORE_EMIT",  # schema exists, ICECORE_CI has no USAGE
              "missing_unguarded": f"ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_MISSING_{run_id.upper()}"}[case]
    res, wall_s = _op("s1_emit_batch", {"run_id": run_id, "proc": target, "n": 1, "attrs": {},
                                        "guard": case != "missing_unguarded"})
    finding = {"run_id": run_id, "proc": target, "call_ok": res.success, "dbt_wall_s": wall_s,
               "warnings": (res.msgs["warn"] + res.msgs["info"])[:3], "errors": res.msgs["error"][:2]}
    record_finding("s1", f"proc.{case}", finding)
    if case == "missing_unguarded":
        assert not res.success, "a bare CALL to a missing procedure fails the run-operation"
    else:
        assert res.success, finding
        assert any("icecore" in w.lower() for w in finding["warnings"]), finding


def test_s1_proc_survives_rollback(dbt, sf, run_id, proc):
    res, _ = _op("s1_emit_rollback", {"run_id": run_id, "proc": proc})
    rows, visible = _wait(sf, run_id, 2) if res.success else ([], None)
    finding = {"run_id": run_id, "call_ok": res.success, "landed": len(rows), "expected": 2,
               "seconds_to_visible": visible, **_shape(rows)}
    if not res.success:
        finding["error"] = " | ".join(res.msgs["error"])[:800]
    record_finding("s1", "proc.rollback", finding)
    assert res.success
    assert len(rows) == 2, finding


def test_s1_proc_levels_off(dbt, sf, run_id, proc):
    """Session LOG_LEVEL/TRACE_LEVEL OFF: the call succeeds; records follow the procedure's database."""
    res, _ = _op("s1_emit_levels_off", {"run_id": run_id, "proc": proc})
    rows, _ = _wait(sf, run_id, 1, timeout_s=150) if res.success else ([], None)
    record_finding("s1", "proc.levels_off", {"run_id": run_id, "call_ok": res.success, "landed": len(rows),
                                             **_shape(rows)})
    assert res.success


@pytest.fixture
def run_proc(dbt, sf, run_id):
    name = f"ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_RUN_{run_id.upper()}"
    res, _ = _op("s1_emit_run_proc_create", {"run_id": run_id})
    assert res.success, res.msgs
    yield name
    sf.cursor().execute(f"drop procedure if exists {name}(ARRAY)")


def test_s1_proc_emit_run_python(dbt, sf, run_id, run_proc):
    """ICECORE_EMIT_RUN: 100 spans with explicit dbt-style timings (1.5 s each, starting 1 hour ago) in one CALL."""
    start_ns = (int(time.time()) - 3600) * 1_000_000_000
    duration_ns = 1_500_000_000
    res, wall_s = _op("s1_emit_run_batch", {"run_id": run_id, "proc": run_proc, "n": 100, "start_ns": start_ns,
                                            "duration_ns": duration_ns, "attrs": ATTRS})
    rows, visible = _wait(sf, run_id, 100) if res.success else ([], None)
    spans = [r for r in rows if r["record_type"] == "SPAN"]

    def ns(ts):
        from datetime import timezone
        return (ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts).timestamp() * 1e9

    timed = sum(1 for r in spans if abs(ns(r["start_timestamp"]) - (start_ns + (r["record_attributes"]["icecore.seq"] - 1)
                                                                       * duration_ns)) < 1e6
                and abs(ns(r["timestamp"]) - (start_ns + r["record_attributes"]["icecore.seq"] * duration_ns)) < 1e6)
    finding = {"run_id": run_id, "call_ok": res.success, "landed": len(spans), "expected": 100,
               "exact_attributes": _exact(spans, {**ATTRS, "icecore.run_id": run_id}), "explicit_times_kept": timed,
               "dbt_wall_s": wall_s, "seconds_to_visible": visible, **_server(sf, run_id), **_shape(spans),
               "warnings": res.msgs["warn"][:2]}
    record_finding("s1", "proc.emit_run_python", finding)
    assert res.success and not res.msgs["warn"], finding
    assert len(spans) == 100 and finding["exact_attributes"] == 100 and timed == 100, finding
    assert finding["scope_names"] == ["icecore"] and finding["snow.executable.type"] == ["PROCEDURE"], finding
