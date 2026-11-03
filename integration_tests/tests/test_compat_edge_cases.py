"""UC8 compat views, edge cases (macros/compat/): an empty event table, a run with no tests, a message over 64 KB.

Sandbox only; every object lives in a run_id-named obs schema (with its `_ELEMENTARY` compat schema) that is dropped
afterwards, and the shared event table is only read through the base filter, which matches nothing in a fresh schema.
- Empty: a fresh obs schema has no icecore records, so every compat view returns zero rows without error.
- No tests: a run that wrote node results and no test results has zero rows in elementary_test_results while the
  views hold rows of another run, and the run's own results show in dbt_run_results.
- Over 64 KB: a node result whose dbt.result.message is 100 KB is capped with its record at payload_cap_kb (default 64),
  and dbt_run_results.message is that truncated text. (Before this story the view's message was a typed NULL and
  NODE_EXECUTIONS had no message column.)
"""
import json
import time
import uuid

import pytest

from helpers.macros import run_macro
from helpers.run_hooks import DATABASE, obs_schema, snowflake_since

pytestmark = pytest.mark.sandbox


def result_item(inv, uid, ntype, status, message, **extra):
    attrs = {"icecore.kind": "node_result", "dbt.invocation_id": inv, "dbt.node.unique_id": uid,
             "dbt.node.type": ntype, "dbt.node.name": uid.split(".")[-1], "dbt.result.status": status,
             "dbt.result.message": message, **extra}
    return {"type": "log", "level": "info", "message": f"{uid} {status}", "attrs": attrs}


def wait_for(sf, sql, args, want, seconds=300):
    deadline = time.monotonic() + seconds
    while True:
        got = sf.cursor().execute(sql, args).fetchone()[0]
        if got == want or time.monotonic() > deadline:
            return got
        time.sleep(5)


@pytest.fixture
def compat_schemas(sf):
    with obs_schema(sf) as obs:
        compat = f"{obs}_ELEMENTARY"
        obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": obs}})]
        try:
            run_macro("it_create_views", {"schema": obs}, obs_vars)
            created = run_macro("it_create_compat_views", {"schema": obs}, obs_vars)
            yield obs, compat, obs_vars, created
        finally:
            sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{compat}" cascade')


def test_every_compat_view_is_empty_on_an_empty_event_table(sf, compat_schemas):
    obs, compat, _, created = compat_schemas
    names = [v["name"] for v in run_macro("it_compat_views_render", {})]
    assert len(names) >= 8 and len(created) == len(names)
    for name in names:
        rows = sf.cursor().execute(f'select * from "{DATABASE}"."{compat}"."{name}"').fetchall()
        assert rows == [], name


def test_a_run_with_no_tests_has_no_elementary_test_results(sf, compat_schemas):
    obs, compat, obs_vars, _ = compat_schemas
    quiet, busy = f"quiet-{uuid.uuid4().hex}", f"busy-{uuid.uuid4().hex}"
    items = [result_item(quiet, "model.p.m1", "model", "success", "SUCCESS 1"),
             result_item(busy, "model.p.m1", "model", "success", "SUCCESS 1"),
             result_item(busy, "test.p.not_null_m1_id", "test", "pass", "ok", **{
                 "dbt.test.name": "not_null_m1_id", "dbt.test.type": "generic", "dbt.test.severity": "ERROR",
                 "dbt.test.failures": 0})]
    run_macro("it_compat_emit_items", {"items": items}, obs_vars)
    core = f'"{DATABASE}"."{obs}"."NODE_EXECUTIONS"'
    assert wait_for(sf, f"select count(*) from {core} where invocation_id in (%s, %s)", (quiet, busy), 3) == 3
    count = lambda view, inv: sf.cursor().execute(  # noqa: E731
        f'select count(*) from "{DATABASE}"."{compat}"."{view}" where invocation_id = %s', (inv,)).fetchone()[0]
    assert count("ELEMENTARY_TEST_RESULTS", busy) == 1  # the view is not empty overall
    assert count("ELEMENTARY_TEST_RESULTS", quiet) == 0
    assert count("DBT_RUN_RESULTS", quiet) == 1


def test_a_message_over_64kb_reads_as_its_truncated_version(sf, compat_schemas):
    obs, compat, obs_vars, _ = compat_schemas
    inv = f"long-{uuid.uuid4().hex}"
    long_message = "x" * (100 * 1024)
    run_macro("it_compat_emit_items", {"items": [
        result_item(inv, "model.p.long", "model", "error", long_message),
        result_item(inv, "model.p.short", "model", "success", "SUCCESS 1")]}, obs_vars)
    core = f'"{DATABASE}"."{obs}"."NODE_EXECUTIONS"'
    assert wait_for(sf, f"select count(*) from {core} where invocation_id = %s", (inv,), 2) == 2
    rows = dict(sf.cursor().execute(
        f'select unique_id, message from "{DATABASE}"."{compat}"."DBT_RUN_RESULTS" where invocation_id = %s', (inv,)).fetchall())
    truncated = rows["model.p.long"]
    assert truncated and 60 * 1024 < len(truncated) < 64 * 1024 and set(truncated) == {"x"}
    assert len(truncated) < len(long_message)
    assert rows["model.p.short"] == "SUCCESS 1"
