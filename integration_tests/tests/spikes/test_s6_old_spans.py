"""Spike S6: the age at which ICECORE_EMIT_RUN records stop landing in the event table (story 2501518a).

PR #84 saw node spans with hours-old start and end times return `ok` and not land. This sends, through
ICECORE_EMIT_RUN in a run_id obs schema, per age: a span entirely at that age, a span that started at that age and
ended now, a span stamped now whose span event is at that age, and (once) a LOG record, which has no time argument.
Read back through the event table, polling on Snowflake's clock. Findings: spikes/results/s6.json, spikes/S6-old-spans.md.
"""
import json
import time

import pytest

from helpers.events import wait_for_events
from helpers.findings import record_finding
from helpers.run_hooks import DATABASE, obs_schema

pytestmark = pytest.mark.sandbox

AGES_MIN = [5, 30, 60, 120, 180, 240, 360, 480, 720, 1080, 1440, 2880, 4320, 10080, 43200]
WHERE = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
         " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
         " and (record_attributes:\"icecore.s6\"::string = %(r)s or startswith(value::string, %(r)s))")


def _now_ns(sf) -> int:
    return int(sf.cursor().execute("select date_part(epoch_nanosecond, current_timestamp())").fetchone()[0])


def _call(sf, schema: str, items: list) -> str:
    cur = sf.cursor()
    return cur.execute(f'call "{DATABASE}"."{schema}".ICECORE_EMIT_RUN(parse_json(%(j)s))',
                       {"j": json.dumps(items)}).fetchone()[0]


def test_s6_old_span_cutoff(sf, run_id):
    now = _now_ns(sf)
    sent = {}
    with obs_schema(sf) as schema:
        for minutes in AGES_MIN:
            start = now - minutes * 60 * 10**9
            tag = {"icecore.s6": run_id, "icecore.age_min": minutes}
            items = [
                {"name": "s6.old_span", "start_ns": start, "end_ns": start + 1_500_000_000,
                 "attrs": {**tag, "icecore.case": "old_span"}},
                {"name": "s6.old_start_end_now", "start_ns": start, "end_ns": now,
                 "attrs": {**tag, "icecore.case": "old_start_end_now"}},
                {"name": "s6.now_span_old_event", "start_ns": now, "end_ns": now + 1_000_000,
                 "attrs": {**tag, "icecore.case": "now_span_old_event"},
                 "events": [{"name": "s6.old_event", "ts_ns": start, "attrs": {**tag, "icecore.case": "old_event"}}]},
            ]
            sent[minutes] = _call(sf, schema, items)
        _call(sf, schema, [{"type": "log", "level": "info", "message": "s6 log",
                            "attrs": {"icecore.s6": run_id, "icecore.case": "log"}}])
        params = {"db": DATABASE, "schema": schema, "r": run_id}
        since = sf.cursor().execute("select dateadd(day, -35, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]
        expected_fast = 1 + sum(2 for m in AGES_MIN if m <= 5)
        wait_for_events(sf, WHERE, params, min_rows=expected_fast, since=since, timeout_s=180, settle_s=0)
        time.sleep(90)  # ingestion lag p95 8 s, worst 20 s (S3): 90 s lets late rows land before absence is concluded
        rows = wait_for_events(sf, WHERE, params, min_rows=0, since=since, timeout_s=0)
        recent = sf.cursor().execute("select dateadd(minute, -10, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]
        in_recent_window = wait_for_events(sf, WHERE, params, min_rows=0, since=recent, timeout_s=0)
    landed = {}
    for r in rows:
        attrs = r["record_attributes"] or {}
        case = attrs.get("icecore.case")
        if case == "log":
            landed.setdefault("log", []).append(r["record_type"])
            continue
        landed.setdefault(int(attrs["icecore.age_min"]), {}).setdefault(case, 0)
        landed[int(attrs["icecore.age_min"])][case] += 1
    table = {str(m): {c: landed.get(m, {}).get(c, 0) for c in ["old_span", "old_start_end_now", "now_span_old_event", "old_event"]}
             for m in AGES_MIN}
    ten_min = {}
    for r in in_recent_window:
        m = (r["record_attributes"] or {}).get("icecore.age_min")
        if m is not None:
            ten_min[str(m)] = ten_min.get(str(m), 0) + 1
    finding = {"call_statuses": sent, "rows_seen_with_a_10_minute_TIMESTAMP_window_per_age_min": ten_min, "landed_per_age_min": table, "log_rows": landed.get("log", []),
               "expected_per_case": 1}
    record_finding("s6", "cutoff", finding)
    assert all(v.startswith("ok") for v in sent.values()), finding
