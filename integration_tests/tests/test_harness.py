"""The harness itself: the sandbox is reachable and findings are recorded."""
import json

import pytest

from helpers import findings
from helpers.events import EVENT_TABLE
from helpers.findings import record_finding


@pytest.mark.sandbox
def test_event_table_is_readable(sf):
    row = sf.cursor().execute(f"select count(*) from {EVENT_TABLE}").fetchone()
    assert isinstance(row[0], int)


@pytest.mark.sandbox
def test_dbt_debug_connects(dbt):
    assert dbt(["debug"]).success


def test_record_finding_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(findings, "RESULTS_DIR", tmp_path)
    record_finding("s0", "a", 1)
    record_finding("s0", "b", 2)
    assert json.load(open(tmp_path / "s0.json")) == {"a": 1, "b": 2}
