"""Offline tests of the harness helpers: they must behave before any Snowflake call."""
import datetime as dt
import json
import os
import threading

import pytest

from helpers import env, events, findings


def test_require_env_names_missing_variables(monkeypatch):
    for key in env.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(pytest.fail.Exception, match="SNOWFLAKE_ACCOUNT"):
        env.require_env()


def test_where_clause_bounds_time_when_since_given():
    since = dt.datetime(2026, 10, 31, 8, 0, 0)
    sql, params = events.build_query("value::string ilike %(p)s", {"p": "%x%"}, since)
    assert "TIMESTAMP >= %(_since)s" in sql and params["_since"] == since and params["p"] == "%x%"


class FakeCursor:
    def __init__(self, batches):
        self.batches, self.calls = batches, 0
    def execute(self, sql, params):
        self.sql, self.params = sql, params
        return self
    def fetchall(self):
        rows = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return rows
    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, batches):
        self.cur = FakeCursor(batches)
    def cursor(self, *_):
        return self.cur


def test_wait_for_events_settles_before_returning_exact_count():
    conn = FakeConn([[{"V": 1}], [{"V": 1}, {"V": 2}], [{"V": 1}, {"V": 2}]])
    rows = events.wait_for_events(conn, "1=1", {}, min_rows=1, poll_s=0, settle_s=0.01)
    assert len(rows) == 2  # a later row that lands during the settle window is included


def test_assert_no_events_fails_when_rows_appear():
    conn = FakeConn([[], [{"V": 1}]])
    with pytest.raises(AssertionError):
        events.assert_no_events(conn, "1=1", {}, wait_s=0.05, poll_s=0)


def test_record_finding_is_safe_under_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setattr(findings, "RESULTS_DIR", tmp_path)
    threads = [threading.Thread(target=findings.record_finding, args=("s0", f"k{i}", i)) for i in range(20)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(json.load(open(tmp_path / "s0.json"))) == 20


def test_placeholder_env_restores_after_an_error_and_keeps_empty_values(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.setenv("SNOWFLAKE_ROLE", "")
    with pytest.raises(RuntimeError):
        with env.placeholder_env():
            assert os.environ["SNOWFLAKE_ROLE"] == "placeholder"
            raise RuntimeError("boom")
    assert "SNOWFLAKE_ACCOUNT" not in os.environ
    assert os.environ["SNOWFLAKE_ROLE"] == ""
