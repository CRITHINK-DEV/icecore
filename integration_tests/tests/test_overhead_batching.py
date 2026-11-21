"""icecore's own statements per run (spikes/GATE2.md, finding 6).

1. Monitor records at run end (macros/telemetry/test_records.sql): a monitor test writes its records into its own compiled
   SQL as one comment, end_run reads them back from `results` and sends them with the run's batch, as span events at the
   time the test made them, instead of one ICECORE_EMIT CALL per test.
2. One history read per run (macros/engines/get_metric_history.sql): on-run-start reads the history of every selected
   monitor test once and keeps it in dbt's graph; a test scores from that copy instead of reading the METRICS view.

Offline tests render through integration_tests/macros/test_support/it_hooks.sql. The sandbox tests run the shop example
twice with its monitor tests, and two builds at once, and compare the records and the statements against the old path.
"""
import datetime as dt
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from dbt_common.invocation import get_invocation_id
from snowflake.connector import DictCursor
from snowflake.connector.util_text import split_statements

from helpers.events import wait_for_events
from helpers.macros import IT_DIR, invoke, node_statuses, run_macro

HOSTILE = "a*/b/*c; $$ it's \\ \n end é"
EVENTS = [
    {"kind": "metric", "level": "info", "attributes": {"dbt.metric.name": "row_count", "dbt.metric.value": 5,
                                                       "dbt.node.relation": HOSTILE}},
    {"kind": "anomaly", "level": "warn", "message": HOSTILE, "attributes": {"dbt.metric.name": "row_count"}},
]
REL = '"DB"."S"."ORDERS"'


def render_records(events=EVENTS, **kwargs):
    return run_macro("it_test_records", {"events": events, **kwargs})


def node_span(items):
    return next(i for i in items if i.get("name") == "icecore.node")


# --- offline: monitor records at run end ----------------------------------------------------------

def test_the_records_come_back_on_the_tests_node_span_with_their_time_and_values():
    out = render_records()
    events = node_span(out["items"])["events"]
    assert [e["name"] for e in events] == ["icecore.metric", "icecore.anomaly"]
    assert events[0]["attrs"]["dbt.node.relation"] == HOSTILE and events[1]["attrs"]["icecore.message"] == HOSTILE
    assert events[0]["attrs"]["dbt.invocation_id"] and events[1]["attrs"]["icecore.level"] == "warn"
    assert isinstance(events[0]["ts_ns"], int) and events[0]["ts_ns"] > 1_700_000_000 * 10**9
    assert not [i for i in out["items"] if i.get("name") == "icecore.monitor"]


def test_no_value_can_end_the_comment_and_the_test_sql_stays_one_statement():
    comment = render_records()["comment"]
    assert comment.startswith("/* icecore:records ") and comment.endswith(" */")
    assert "*" not in comment[2:-2]
    sql = f"select count(*) from (\n  {comment}\n  select 1 where false\n) t"
    assert len([s for s, _ in split_statements(io.StringIO(sql)) if s.strip()]) == 1


def test_records_are_stamped_on_snowflakes_clock_when_start_run_measured_the_offset():
    plain = render_records()
    ahead = render_records(offset_ns=-18 * 10**9)  # dbt's clock 18 s ahead of Snowflake's, as seen in the sandbox
    assert 0 <= node_span(plain["items"])["events"][0]["ts_ns"] - plain["before_ns"] < 5 * 10**9
    shifted = node_span(ahead["items"])["events"][0]["ts_ns"] - ahead["before_ns"]
    assert -19 * 10**9 < shifted < -13 * 10**9


def test_records_of_another_invocation_are_never_taken():
    assert node_span(render_records(foreign=True)["items"])["events"] == []


def test_a_test_without_a_node_span_sends_them_on_a_monitor_span():
    items = render_records(span=False)["items"]
    monitor = [i for i in items if i.get("name") == "icecore.monitor"]
    assert len(monitor) == 1 and monitor[0]["attrs"] == {"dbt.node.unique_id": "test.p.mon"}
    assert [e["name"] for e in monitor[0]["events"]] == ["icecore.metric", "icecore.anomaly"]
    assert monitor[0]["start_ns"] == monitor[0]["events"][0]["ts_ns"]


def test_a_span_carries_at_most_100_records_and_the_rest_go_on_monitor_spans():
    many = [{"kind": "metric", "attributes": {"dbt.metric.name": f"m{i}", "dbt.metric.value": i}} for i in range(250)]
    items = render_records(events=many)["items"]
    assert len(node_span(items)["events"]) == 100
    assert [len(i["events"]) for i in items if i.get("name") == "icecore.monitor"] == [100, 50]
    names = [e["attrs"]["dbt.metric.name"] for i in items if i.get("name") in ("icecore.node", "icecore.monitor") for e in i["events"]]
    assert names == [f"m{i}" for i in range(250)]


def test_the_integration_project_hook_is_recognised_as_end_run_with_results():
    assert render_records()["collects"] is True


# --- offline: one history read per run ------------------------------------------------------------

def source(**kwargs):
    args = {"test_id": "test.p.mon", "relation": REL, "metric": "row_count", **kwargs}
    return run_macro("it_history_source", args)


def test_a_test_in_the_runs_copy_scores_from_it_through_the_same_window():
    out = source(points=[["2026-11-01T00:00:00.000000000", 10.0], ["2026-11-02T00:00:00.000000000", 12.5]])
    assert "flatten(input => parse_json(" in out["source"] and "METRICS" not in out["sql"]
    assert '["2026-11-01T00:00:00.000000000", 10.0]' in out["source"]
    assert "observed_at <= dateadd(minute, -1" in out["sql"] and "observed_at > dateadd(day, -30" in out["sql"]
    assert "and node_unique_id = 'test.p.mon'" in out["sql"]
    assert out["forgotten"] is True


def test_a_series_with_no_history_reads_an_empty_copy_not_the_view():
    out = source(metric="null_rate", column="EMAIL")
    assert "parse_json('[]')" in out["source"]


def test_the_view_is_read_when_the_run_has_no_copy_for_the_test_or_its_lookback():
    assert source(seed=False)["source"] is None
    assert source(test_id="test.p.other")["source"] is None
    assert source(lookback_days=60)["source"] is None


def test_a_hostile_relation_or_column_stays_data_in_one_statement():
    out = source(relation=HOSTILE, column=HOSTILE, dimension=HOSTILE)
    sql = f"select count(*) from ({out['sql']})"
    assert len([s for s, _ in split_statements(io.StringIO(sql)) if s.strip()]) == 1
    assert "$" not in out["source"]


# --- sandbox --------------------------------------------------------------------------------------

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
TESTS = ("it_freshness_bare", "it_freshness_pass", "it_volume_bare", "it_volume_in_range")


@pytest.fixture(scope="module")
def batched(dbt, sf):
    """The same four monitor tests run four times against an obs schema of their own: once the old way (telemetry off:
    each test CALLs ICECORE_EMIT and reads METRICS itself), once the new way, then twice at once in two processes."""
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    fresh, vol = f"IT_FRESH_{run_id}".upper(), f"IT_VOL_{run_id}".upper()
    names = {"it_uc2_fresh_table": fresh, "it_uc2_vol_table": vol}
    cur = sf.cursor()
    now = lambda: cur.execute("select current_timestamp()").fetchone()[0]  # noqa: E731
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        obs = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
        run_macro("it_create_emit_procedures", {}, obs)
        run_macro("it_create_views", {"schema": schema}, obs)
        for table in (fresh, vol):
            cur.execute(f'create table "{DATABASE}"."SPIKES"."{table}" (id number)')
            cur.execute(f'insert into "{DATABASE}"."SPIKES"."{table}" select seq4() from table(generator(rowcount => 100))')
        since, since_utc = now(), cur.execute("select sysdate()").fetchone()[0]
        args = ["test", "--select", *TESTS]
        old = invoke(args + ["--vars", json.dumps({"icecore": {"obs_schema": schema, "telemetry": False}, **names})])
        old_id, between = get_invocation_id(), now()
        new = invoke(args + ["--vars", json.dumps({"icecore": {"obs_schema": schema}, **names})])
        new_id, after_new = get_invocation_id(), now()
        dbt_cli = str(Path(sys.executable).parent / "dbt")
        procs = [subprocess.Popen([dbt_cli, *args, "--vars", json.dumps({"icecore": {"obs_schema": schema}, **names}),
                                   "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR),
                                   "--target-path", str(IT_DIR / f"target_{run_id}_{i}"), "--log-path", str(IT_DIR / f"logs_{run_id}_{i}")],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(2)]
        codes = [p.wait(timeout=600) for p in procs]
        events = wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
                " and record:name::string in ('icecore.metric', 'icecore.node')",
            {"db": DATABASE, "schema": schema}, min_rows=4 * 4 + 3 * 4, since=since_utc, settle_s=15)
        history = sf.cursor(DictCursor).execute(
            "select query_text, start_time from table(information_schema.query_history_by_user(user_name => current_user(), "
            "end_time_range_start => %(t)s::timestamp_ltz, result_limit => 10000)) where contains(query_text, %(schema)s)",
            {"t": since, "schema": schema}).fetchall()
        yield {"old": old, "new": new, "old_id": old_id, "new_id": new_id, "codes": codes, "events": events,
               "history": history, "since": since, "between": between, "after_new": after_new}
    finally:
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')
        for table in (fresh, vol):
            cur.execute(f'drop table if exists "{DATABASE}"."SPIKES"."{table}"')
        for i in range(2):
            subprocess.run(["rm", "-rf", str(IT_DIR / f"target_{run_id}_{i}"), str(IT_DIR / f"logs_{run_id}_{i}")])


def metrics(b, invocation_id=None):
    return [e for e in b["events"] if e["record"]["name"] == "icecore.metric"
            and (invocation_id is None or e["record_attributes"]["dbt.invocation_id"] == invocation_id)]


def unique_id_of(e) -> str:
    return e["record_attributes"]["dbt.node.unique_id"]


@pytest.mark.sandbox
def test_the_same_records_land_as_before_with_the_time_the_test_made_them(batched):
    assert node_statuses(batched["old"]) == node_statuses(batched["new"]) == {t: "pass" for t in TESTS}
    old, new = metrics(batched, batched["old_id"]), metrics(batched, batched["new_id"])
    assert len(old) == len(new) == 4 and sorted(map(unique_id_of, old)) == sorted(map(unique_id_of, new))
    by_test = {unique_id_of(e): e for e in old}
    for e in new:
        before = by_test[unique_id_of(e)]
        assert e["record_type"] == before["record_type"] == "SPAN_EVENT"
        assert set(e["record_attributes"]) == set(before["record_attributes"])
        if e["record_attributes"]["dbt.metric.name"] == "row_count":
            assert e["record_attributes"]["dbt.metric.value"] == before["record_attributes"]["dbt.metric.value"] == 100
        # on the test's node span, stamped on Snowflake's clock while its run ran (dbt's clock may be off: 18 s ahead in
        # the sandbox), so between Snowflake's time before and after that run
        span = next(s for s in batched["events"] if s["record"]["name"] == "icecore.node" and s["trace"]["span_id"] == e["trace"]["span_id"])
        assert unique_id_of(span) == unique_id_of(e)
        utc = lambda t: t.astimezone(dt.timezone.utc).replace(tzinfo=None)  # noqa: E731
        assert utc(batched["between"]) <= e["timestamp"] <= utc(batched["after_new"])


@pytest.mark.sandbox
def test_a_run_sends_no_emit_call_per_test_and_reads_the_history_once(batched):
    def statements(lo, hi):
        texts = [h["QUERY_TEXT"] for h in batched["history"] if lo <= h["START_TIME"] <= hi]
        top = [t for t in texts if t.lower().startswith("execute immediate")]
        return {"emit": sum('"ICECORE_EMIT"(' in t for t in top), "metrics_reads": sum('"METRICS"' in t for t in top)}
    # telemetry off (the old path): no run start, and each test CALLs and reads on its own
    assert statements(batched["since"], batched["between"]) == {"emit": 4, "metrics_reads": 4}
    # the run start CALL, and the one history read of on-run-start
    assert statements(batched["between"], batched["after_new"]) == {"emit": 1, "metrics_reads": 1}


@pytest.mark.sandbox
def test_two_runs_at_once_keep_their_records_apart(batched):
    assert batched["codes"] == [0, 0]
    nodes = {e["trace"]["span_id"]: e for e in batched["events"] if e["record"]["name"] == "icecore.node"}
    runs = {}
    for e in metrics(batched):
        if e["record_attributes"]["dbt.invocation_id"] in (batched["old_id"], batched["new_id"]):
            continue
        assert nodes[e["trace"]["span_id"]]["record_attributes"]["dbt.invocation_id"] == e["record_attributes"]["dbt.invocation_id"]
        runs.setdefault(e["record_attributes"]["dbt.invocation_id"], []).append(unique_id_of(e))
    assert len(runs) == 2 and all(len(ids) == 4 and len(set(ids)) == 4 for ids in runs.values()), runs
