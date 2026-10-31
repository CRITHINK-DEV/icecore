"""Spike S1: compare the ways a dbt macro can write telemetry to the event table.

Every probe runs through `dbt run-operation`, so it reaches Snowflake through `run_query`
exactly as the package will. Each probe is run two ways (`via`):

- `anonymous`: what the Phase 1 plan specifies. E1 is an anonymous Snowflake Scripting
  block (`EXECUTE IMMEDIATE $$ ... $$`), E2 an anonymous Python procedure
  (`WITH p AS PROCEDURE ... CALL p(...)`).
- `temp_procedure`: the same body in a session-scoped `CREATE TEMPORARY PROCEDURE` in
  SPIKES, called once. Added because exploration showed anonymous statements land nothing
  in the database's event table.

E3 (hybrid table, the spec's fallback) is measured by `test_s1_hybrid_table_fallback`.

A failing probe is a finding: every test records what it saw in spikes/results/s1.json
before asserting.
"""
import json
import re
import time
from collections import Counter
from datetime import timezone
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
VIAS = ["anonymous", "temp_procedure"]
TIMEOUT_S = 180

WHERE = (
    "timestamp > dateadd(hour, -6, current_timestamp()) and "
    "(record_attributes:\"icecore.run_id\" = %(r)s or value::string ilike %(p)s)"
)

pytestmark = [pytest.mark.sandbox, pytest.mark.parametrize("via", VIAS)]


def _op(dbt, macro: str, args: dict):
    """dbt run-operation (the `dbt` fixture has run deps). Error text is kept on res.errors:
    dbtRunnerResult carries no message for a failed run-operation, only the event stream does."""
    errors: list[str] = []

    def keep_errors(ev):
        if ev.info.level == "error" and ev.info.msg:
            errors.append(str(ev.info.msg))

    t0 = time.monotonic()
    res = dbtRunner(callbacks=[keep_errors]).invoke(
        ["run-operation", macro, "--args", json.dumps(args), "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])
    res.errors = errors
    return res, round(time.monotonic() - t0, 2)


def _error(res) -> str:
    text = " | ".join(getattr(res, "errors", []) or []) or (str(res.exception) if res.exception else "")
    return (text or "run-operation failed")[:800]


def _wait(sf, run_id: str, min_rows: int, timeout_s: int = TIMEOUT_S, settle_s: int = 0):
    """Rows tagged with run_id, waiting for min_rows; never raises (a shortfall is a finding)."""
    t0 = time.monotonic()
    params = {"r": run_id, "p": f"%{run_id}%"}
    try:
        rows = wait_for_events(sf, WHERE, params, min_rows=min_rows, timeout_s=timeout_s)
        visible_s = round(time.monotonic() - t0, 1)
    except TimeoutError:
        visible_s = None
    if settle_s:
        time.sleep(settle_s)  # stragglers after the expected count, to catch duplicates
    rows = wait_for_events(sf, WHERE, params, min_rows=0, timeout_s=0)
    return rows, visible_s


def _get(d, key, default=None):
    return d.get(key, default) if isinstance(d, dict) else default


def _describe(rows: list) -> dict:
    res_keys = set()
    for r in rows:
        res_keys |= set((_get(r, "resource_attributes") and r["resource_attributes"].keys()) or [])
    ts = sorted(r["timestamp"] for r in rows if r.get("timestamp") is not None)
    return {
        "rows": len(rows),
        "record_types": dict(Counter(r["record_type"] for r in rows)),
        "scope_names": sorted({str(_get(r["scope"], "name")) for r in rows}),
        "record_names": sorted({str(_get(r["record"], "name")) for r in rows if _get(r["record"], "name")}),
        "resource_attribute_keys": sorted(res_keys),
        "has_snow_query_id": bool(rows) and all(_get(r["resource_attributes"], "snow.query.id") for r in rows),
        "snow_query_ids": sorted({str(_get(r["resource_attributes"], "snow.query.id")) for r in rows}),
        "executable_types": sorted({str(_get(r["resource_attributes"], "snow.executable.type")) for r in rows}),
        "sdk_languages": sorted({str(_get(r["resource_attributes"], "telemetry.sdk.language")) for r in rows}),
        "trace_ids": sorted({str(_get(r["trace"], "trace_id")) for r in rows}),
        "span_ids": len({str(_get(r["trace"], "span_id")) for r in rows}),
        "first_timestamp": str(ts[0]) if ts else None,
        "last_timestamp": str(ts[-1]) if ts else None,
    }


_COMMENT = re.compile(r"^\s*/\*.*?\*/\s*", re.S)


def _server_queries(sf, run_id: str) -> dict:
    """Statements dbt sent for this run_id, from INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER."""
    rows = sf.cursor(DictCursor).execute(
        "select query_id, query_type, execution_status, total_elapsed_time, query_text "
        "from table(information_schema.query_history_by_user("
        "end_time_range_start => dateadd(minute, -30, current_timestamp()), result_limit => 10000)) "
        "where query_text ilike %(p)s and query_text not ilike '%%TELEMETRY.EVENTS%%' "
        "and query_text not ilike '%%query_history%%' order by start_time",
        {"p": f"%{run_id}%"},
    ).fetchall()
    stmts = []
    for r in rows:
        text = _COMMENT.sub("", r["QUERY_TEXT"])
        stmts.append({"type": r["QUERY_TYPE"], "status": r["EXECUTION_STATUS"], "ms": r["TOTAL_ELAPSED_TIME"],
                      "query_id": r["QUERY_ID"], "head": " ".join(text.split())[:48]})
    top = [s for s in stmts if re.match(r"(EXECUTE IMMEDIATE|CALL|WITH|CREATE|BEGIN|ROLLBACK|INSERT)", s["head"], re.I)]
    return {"statements": stmts, "emit_ms": sum(s["ms"] or 0 for s in top)}


def _measure(dbt, sf, run_id, key, macro, args, expected, settle_s=10):
    res, wall_s = _op(dbt, macro, args)
    rows, visible_s = _wait(sf, run_id, expected, timeout_s=TIMEOUT_S if res.success else 0,
                           settle_s=settle_s if res.success else 0)
    finding = {"run_id": run_id, "call_ok": res.success, "expected": expected, "landed": len(rows), "dbt_wall_s": wall_s,
               "seconds_to_visible": visible_s, **_describe(rows), "server": _server_queries(sf, run_id)}
    if not res.success:
        finding["error"] = _error(res)
    record_finding("s1", key, finding)
    return res, rows, finding


def _subset(have: dict, want: dict) -> bool:
    return all(k in (have or {}) and have[k] == v and type(have[k]) is type(v) for k, v in want.items())


def test_s1_sql_log_lands(dbt, sf, run_id, via):
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.e1_sql_log", "s1_sql_log",
                            {"run_id": run_id, "n": 100, "via": via}, expected=100)
    assert res.success
    logs = [r for r in rows if r["record_type"] == "LOG"]
    assert len(logs) == 100, f
    assert all(_get(r["resource_attributes"], "snow.query.id") for r in logs)


def test_s1_sql_event_carries_attributes(dbt, sf, run_id, via):
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.e1_sql_event", "s1_sql_event",
                            {"run_id": run_id, "n": 100, "attrs": ATTRS, "via": via}, expected=100)
    assert res.success
    events = [r for r in rows if r["record_type"] == "SPAN_EVENT"]
    want = {**ATTRS, "icecore.run_id": run_id}
    exact = [r for r in events if r["record_attributes"] == want and _subset(r["record_attributes"], want)]
    if events:
        sample = events[0]["record_attributes"]
        record_finding("s1", f"{via}.e1_sql_event.attribute_types",
                       {k: type(v).__name__ for k, v in (sample or {}).items()})
    assert len(events) == 100, f
    assert len(exact) == 100, events[0]["record_attributes"]


def test_s1_sql_span_attrs_land(dbt, sf, run_id, via):
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.e1_sql_span_attrs", "s1_sql_span_attrs",
                            {"run_id": run_id, "attrs": ATTRS, "via": via}, expected=1)
    assert res.success
    spans = [r for r in rows if r["record_type"] == "SPAN" and _subset(r["record_attributes"], ATTRS)]
    if spans:
        record_finding("s1", f"{via}.e1_sql_span_attrs.span_name", _get(spans[0]["record"], "name"))
    assert len(spans) == 1, f


def test_s1_python_logs_carry_attributes_and_scope(dbt, sf, run_id, via):
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.e2_python_logs", "s1_python",
                            {"run_id": run_id, "n_logs": 100, "n_spans": 0, "attrs": ATTRS, "via": via},
                            expected=100)
    assert res.success
    logs = [r for r in rows if r["record_type"] == "LOG"]
    if logs:
        record_finding("s1", f"{via}.e2_python_logs.sample_record_attributes", logs[0]["record_attributes"])
    assert len(logs) == 100, f
    assert all(_subset(r["record_attributes"], {**ATTRS, "icecore.run_id": run_id}) for r in logs)
    assert all(_get(r["scope"], "name") == "icecore" for r in logs)


def test_s1_python_custom_spans(dbt, sf, run_id, via):
    # 10 spans + 10 span events
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.e2_python_spans", "s1_python",
                            {"run_id": run_id, "n_logs": 0, "n_spans": 10, "attrs": ATTRS, "via": via},
                            expected=20)
    assert res.success
    spans = [r for r in rows if r["record_type"] == "SPAN" and _get(r["record"], "name") == "icecore.node"]
    events = [r for r in rows if r["record_type"] == "SPAN_EVENT"]
    per_span = Counter(_get(e["trace"], "span_id") for e in events)
    if spans:
        s = spans[0]
        record_finding("s1", f"{via}.e2_python_spans.sample_span",
                       {"record": s["record"], "record_attributes": s["record_attributes"],
                        "start_timestamp": str(s["start_timestamp"]), "timestamp": str(s["timestamp"])})
    assert len(spans) == 10, f
    assert all(per_span[_get(s["trace"], "span_id")] == 1 for s in spans)


def test_s1_python_span_explicit_timings(dbt, sf, run_id, via):
    """Spans written after the fact with dbt-style timings: 3 spans of 4.2 s starting 2 hours ago
    (inside the 6-hour window the reads use; exploration showed a span 5 days back is kept too)."""
    start_ns = (int(time.time()) - 2 * 3600) * 1_000_000_000
    duration_ns = 4_200_000_000
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.e2_python_span_timings", "s1_python",
                            {"run_id": run_id, "n_logs": 0, "n_spans": 3, "attrs": {}, "via": via,
                             "span_start_ns": start_ns, "span_duration_ns": duration_ns}, expected=6)
    assert res.success
    spans = sorted((r for r in rows if r["record_type"] == "SPAN"),
                   key=lambda r: _get(r["record_attributes"], "icecore.span_index"))
    got = [{"start": str(r["start_timestamp"]), "end": str(r["timestamp"])} for r in spans]
    want = [{"start_ns": start_ns + i * duration_ns, "end_ns": start_ns + (i + 1) * duration_ns} for i in range(3)]
    def ns(ts):  # event table timestamps are TIMESTAMP_NTZ in UTC
        return (ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts).timestamp() * 1e9

    ok = len(spans) == 3 and all(
        abs(ns(r["start_timestamp"]) - w["start_ns"]) < 1e6 and abs(ns(r["timestamp"]) - w["end_ns"]) < 1e6
        for r, w in zip(spans, want))
    record_finding("s1", f"{via}.e2_python_span_timings.check", {"spans": got, "timings_kept": ok})
    assert ok, got


def test_s1_trace_grouping(dbt, sf, run_id, via):
    oks, errs = [], []
    for i in (1, 2):
        res, _ = _op(dbt, "s1_sql_event", {"run_id": run_id, "n": 1, "attrs": {"icecore.call": i}, "via": via})
        oks.append(res.success)
        if not res.success:
            errs.append(_error(res))
    rows, visible_s = _wait(sf, run_id, 2)
    events = [r for r in rows if r["record_type"] == "SPAN_EVENT"]
    traces = sorted({str(_get(r["trace"], "trace_id")) for r in events})
    finding = {"run_id": run_id, "calls_ok": oks, "errors": errs, "landed": len(events), "trace_ids": traces,
               "shared_trace": len(events) == 2 and len(traces) == 1,
               "query_ids": sorted({str(_get(r["resource_attributes"], "snow.query.id")) for r in events}),
               "trace_id_prefix_matches_query_id": [
                   str(_get(r["trace"], "trace_id"))[:8] == str(_get(r["resource_attributes"], "snow.query.id"))[:8]
                   for r in events]}
    record_finding("s1", f"{via}.trace_grouping", finding)
    assert all(oks)
    assert len(events) == 2, finding


def test_s1_escaping_roundtrip(dbt, sf, run_id, via):
    attrs = {"icecore.text": WEIRD, "icecore.dollars": DOLLARS}
    results = {
        "e1_log": _op(dbt, "s1_sql_log", {"run_id": run_id, "n": 1, "message": WEIRD, "via": via})[0],
        "e1_event": _op(dbt, "s1_sql_event", {"run_id": run_id, "n": 1, "attrs": attrs, "via": via})[0],
        "e2": _op(dbt, "s1_python", {"run_id": run_id, "n_logs": 1, "n_spans": 0, "attrs": attrs,
                                     "message": WEIRD, "via": via})[0],
    }
    calls = {k: r.success for k, r in results.items()}
    errors = {k: _error(r) for k, r in results.items() if not r.success}
    rows, _ = _wait(sf, run_id, 3)
    sql_logs = [r for r in rows if r["record_type"] == "LOG" and _get(r["scope"], "name") != "icecore"]
    py_logs = [r for r in rows if r["record_type"] == "LOG" and _get(r["scope"], "name") == "icecore"]
    events = [r for r in rows if r["record_type"] == "SPAN_EVENT"]
    want_msg = f"{WEIRD} {run_id} 1"
    seen = {
        "e1_log_message": sql_logs[0]["value"] == want_msg if sql_logs else None,
        "e1_event_attrs": _subset(events[0]["record_attributes"], attrs) if events else None,
        "e2_log_message": py_logs[0]["value"] == want_msg if py_logs else None,
        "e2_log_attrs": _subset(py_logs[0]["record_attributes"], attrs) if py_logs else None,
    }
    record_finding("s1", f"{via}.escaping", {"run_id": run_id, "calls_ok": calls, "errors": errors, "roundtrip": seen,
                                             "landed": len(rows)})
    assert all(calls.values()), calls
    assert all(v is True for v in seen.values()), seen


def test_s1_emission_with_levels_off_does_not_fail(dbt, sf, run_id, via):
    """Each probe in its own run-operation with the session's LOG_LEVEL and TRACE_LEVEL OFF."""
    calls = {}
    for probe in ["sql_log", "sql_event", "sql_span_attrs", "python"]:
        res, wall_s = _op(dbt, "s1_levels_off", {"run_id": run_id, "via": via, "probes": [probe]})
        calls[probe] = {"call_ok": res.success, "dbt_wall_s": wall_s}
        if not res.success:
            calls[probe]["error"] = _error(res)
    rows, _ = _wait(sf, run_id, 1, timeout_s=150)
    record_finding("s1", f"{via}.levels_off", {"run_id": run_id, "calls": calls, "landed": len(rows), **_describe(rows)})
    assert all(c["call_ok"] for c in calls.values()), calls


def test_s1_survives_rollback(dbt, sf, run_id, via):
    res, rows, f = _measure(dbt, sf, run_id, f"{via}.rollback", "s1_in_rollback",
                            {"run_id": run_id, "via": via}, expected=2)
    assert res.success
    types = Counter(r["record_type"] for r in rows)
    assert types["SPAN_EVENT"] >= 1 and types["LOG"] >= 1, f


def test_s1_hybrid_table_fallback(dbt, sf, run_id, via):
    """E3: 100 rows into a hybrid table in SPIKES, plus one insert inside a rolled-back transaction."""
    if via != "anonymous":
        pytest.skip("E3 is a single mechanism; measured once")
    table = f"ICECORE_SANDBOX.SPIKES.S1_H_{run_id.upper()}"
    try:
        res, wall_s = _op(dbt, "s1_hybrid", {"run_id": run_id, "n": 100, "attrs": ATTRS})
        server = _server_queries(sf, run_id)
        counts = {}
        if res.success:
            counts = dict(sf.cursor().execute(f"select kind, count(*) from {table} group by kind").fetchall())
        finding = {"run_id": run_id, "call_ok": res.success, "dbt_wall_s": wall_s, "rows_by_kind": counts,
                   "server": server}
        if not res.success:
            finding["error"] = _error(res)
        record_finding("s1", "e3_hybrid_table", finding)
    finally:
        sf.cursor().execute(f"drop table if exists {table}")
    assert res.success
    assert counts.get("committed") == 100, counts
    assert counts.get("rolled_back", 0) == 0, counts


# --- Routing: which event table receives the record -------------------------------------------

ROUTING_VARIANTS = ["anon_session_db", "anon_use_db_inside", "anon_calls_qualified_proc",
                    "anon_python_min_packages", "temp_proc"]
ACCOUNT_TABLES = ["SNOWFLAKE.TELEMETRY.EVENTS_VIEW", "SNOWFLAKE.TELEMETRY.EVENTS"]


def _no_database_connection():
    import os

    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    )


def _no_database_control(tag: str) -> dict:
    """The same anonymous block from a session with no current database."""
    attrs = json.dumps({"icecore.run_id": tag, "icecore.routing": "anon_no_database"})
    block = (
        "EXECUTE IMMEDIATE $$\nDECLARE\n  a OBJECT;\nBEGIN\n"
        f"  a := PARSE_JSON('{attrs}');\n"
        "  SYSTEM$ADD_EVENT('icecore.probe', :a);\n"
        f"  SYSTEM$LOG_INFO('routing anon_no_database {tag} 1');\n"
        "  RETURN 'ok';\nEND;\n$$"
    )
    conn = _no_database_connection()
    try:
        cur = conn.cursor()
        current_db = cur.execute("select current_database()").fetchone()[0]
        cur.execute("ALTER SESSION SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS'")
        cur.execute(block)
        return {"call_ok": True, "current_database": current_db, "query_id": cur.sfqid}
    except Exception as e:  # a failure is a finding
        return {"call_ok": False, "error": str(e)[:500]}
    finally:
        conn.close()


def _read_account_table(sf, run_id: str) -> dict:
    """Rows for run_id in the account event table, or why it cannot be read (ICECORE_CI may lack access)."""
    errors = {}
    for table in ACCOUNT_TABLES:
        try:
            rows = sf.cursor(DictCursor).execute(
                "select record_type, coalesce(record_attributes:\"icecore.run_id\"::string, value::string) as tag, "
                "resource_attributes:\"snow.database.name\"::string as db, "
                "resource_attributes:\"snow.query.id\"::string as query_id "
                f"from {table} where timestamp > dateadd(hour, -6, current_timestamp()) "
                "and (record_attributes:\"icecore.run_id\"::string like %(rp)s or value::string ilike %(p)s)",
                {"rp": f"{run_id}%", "p": f"%{run_id}%"},
            ).fetchall()
            return {"readable": table, "rows": rows}
        except Exception as e:
            errors[table] = str(e).splitlines()[-1][:300]
    return {"readable": False, "errors": errors}


def test_s1_routing_by_database_context(dbt, sf, run_id, via):
    if via != "anonymous":
        pytest.skip("routing variants are listed explicitly; measured once")
    tags = {v: f"{run_id}r{i}" for i, v in enumerate(ROUTING_VARIANTS + ["anon_no_database"])}
    calls = {}
    for v in ROUTING_VARIANTS:
        res, wall_s = _op(dbt, "s1_routing", {"run_id": tags[v], "variant": v})
        calls[v] = {"call_ok": res.success, "dbt_wall_s": wall_s}
        if not res.success:
            calls[v]["error"] = _error(res)
    calls["anon_no_database"] = _no_database_control(tags["anon_no_database"])

    # The temporary-procedure baseline is known to land: wait for it, then give the others time too.
    where = ("timestamp > dateadd(hour, -6, current_timestamp()) and "
             "(record_attributes:\"icecore.run_id\"::string like %(rp)s or value::string ilike %(p)s)")
    params = {"rp": f"{run_id}%", "p": f"%{run_id}%"}
    try:
        wait_for_events(sf, where + " and (record_attributes:\"icecore.run_id\"::string = %(b)s "
                        "or value::string ilike %(bp)s)",
                        {**params, "b": tags["temp_proc"], "bp": f"%{tags['temp_proc']}%"}, min_rows=2,
                        timeout_s=TIMEOUT_S)
    except TimeoutError:
        pass
    time.sleep(60)  # the anonymous variants were emitted before the baseline; this is extra margin
    rows = wait_for_events(sf, where, params, min_rows=0, timeout_s=0)
    account = _read_account_table(sf, run_id)

    def tag_of(text) -> str | None:
        for v, t in tags.items():
            if text and (text == t or f" {t} " in f" {text} "):
                return v
        return None

    result = {}
    for v in tags:
        mine = [r for r in rows if tag_of(_get(r["record_attributes"], "icecore.run_id") or r["value"]) == v]
        entry = {**calls[v], "tag": tags[v],
                 "sandbox_table": {"rows": len(mine),
                                   "record_types": dict(Counter(r["record_type"] for r in mine)),
                                   "snow.database.name": sorted({str(_get(r["resource_attributes"],
                                                                          "snow.database.name")) for r in mine}),
                                   "snow.executable.type": sorted({str(_get(r["resource_attributes"],
                                                                            "snow.executable.type")) for r in mine})},
                 "server": _server_queries(sf, tags[v])}
        if account["readable"]:
            acc = [r for r in account["rows"] if tag_of(r["TAG"]) == v]
            entry["account_table"] = {"rows": len(acc), "record_types": dict(Counter(r["RECORD_TYPE"] for r in acc)),
                                      "snow.database.name": sorted({str(r["DB"]) for r in acc})}
        else:
            entry["account_table"] = "not readable by ICECORE_CI"
        result[v] = entry
    record_finding("s1", "routing", {"account_table_access": account if not account["readable"]
                                     else {"readable": account["readable"]}, "variants": result})
    assert result["temp_proc"]["sandbox_table"]["rows"] == 2, result["temp_proc"]
