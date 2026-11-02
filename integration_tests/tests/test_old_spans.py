"""A long run's old node timings lose no record (story 2501518a, spikes/S6-old-spans.md).

PR #84 saw `ICECORE_EMIT_RUN returns ok but old node spans do not land`. S6 measured it: records with explicit times land
at every age tried (5 minutes to 30 days, spans, span events); what was missing was the reader's window. A node span's
TIMESTAMP is its end time, so a query that windows on TIMESTAMP for the last 10 minutes cannot see a node that ended
hours ago. Nothing in the writer needs a fallback, so none exists: this pins that end_run sends every node of a long run
with dbt's own times, and that all of it lands.
"""
import calendar
import datetime as dt
import json

import pytest

from helpers.macros import run_macro, run_macro_with_logs
from helpers.run_hooks import icecore_vars, invocation_records, obs_schema, snowflake_since

HOURS_OLD = [30, 12, 3, 1]


def ms_of_ntz(value: dt.datetime) -> int:
    return calendar.timegm(value.utctimetuple()) * 1000 + value.microsecond // 1000


def ns(text: str) -> int:
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return calendar.timegm(value.utctimetuple()) * 10**9 + value.microsecond * 1000


# --- offline ---------------------------------------------------------------------------------------

def test_a_long_run_sends_every_node_with_its_own_old_times():
    records = run_macro("it_old_items", {"hours_old": HOURS_OLD})
    nodes = [r for r in records if r.get("name") == "icecore.node"]
    assert sorted(r["attrs"]["dbt.node.unique_id"] for r in nodes) == sorted(f"model.long.m_{i}" for i in range(1, 5))
    for r in nodes:
        assert r["start_ns"] == ns(r["attrs"]["dbt.node.started_at"]) and r["end_ns"] == ns(r["attrs"]["dbt.node.completed_at"])
    oldest = min(r["start_ns"] for r in nodes)
    assert max(r["end_ns"] for r in records if r.get("name") == "icecore.invocation") - oldest > 29 * 3600 * 10**9
    kinds = [r["attrs"].get("icecore.kind") for r in records if r.get("type") == "log"]
    assert kinds.count("node_result") == len(HOURS_OLD) and kinds.count("run_end") == 1


# --- sandbox ---------------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_old_node_records_all_land(sf):
    with obs_schema(sf) as schema:
        since = snowflake_since(sf, minutes=60 * 40)
        out, _ = run_macro_with_logs("it_old_end_run", {"hours_old": HOURS_OLD}, ["--vars", json.dumps(icecore_vars(schema))])
        assert all(str(s).startswith("ok") for s in out["statuses"]), out
        rows = invocation_records(sf, schema, out["invocation_id"], min_rows=out["records"], since=since)
        spans = [r for r in rows if r["record_type"] == "SPAN" and r["record"].get("name") == "icecore.node"]
        assert sorted(r["record_attributes"]["dbt.node.unique_id"] for r in spans) == \
            sorted(f"model.long.m_{i}" for i in range(1, 5)), "a node span of the long run is missing"
        results = [r for r in rows if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "node_result"]
        assert len(results) == len(HOURS_OLD)
        for r in spans:
            a = r["record_attributes"]
            assert ms_of_ntz(r["start_timestamp"]) == ns(a["dbt.node.started_at"]) // 10**6
            assert ms_of_ntz(r["timestamp"]) == ns(a["dbt.node.completed_at"]) // 10**6
        # The cause in PR #84: a reader that windows on TIMESTAMP for the last 10 minutes does not see them.
        recent = invocation_records(sf, schema, out["invocation_id"], min_rows=0, since=snowflake_since(sf, minutes=10), settle_s=0)
        assert not [r for r in recent if r["record_type"] == "SPAN" and r["record"].get("name") == "icecore.node"]
