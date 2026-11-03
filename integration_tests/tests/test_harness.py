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


@pytest.mark.sandbox
def test_wait_for_events_round_trip(sf, run_id):
    # Anonymous blocks write to the account-level event table (measured 31 Oct, see spike S1), so the
    # round trip uses a procedure in ICECORE_SANDBOX: objects in the database are routed to its event table.
    import datetime as dt
    from helpers.events import wait_for_events

    proc = f"ICECORE_SANDBOX.SPIKES.HARNESS_PROBE_{run_id.upper()}"
    since = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(minutes=1)
    cur = sf.cursor()
    try:
        cur.execute(f"""create procedure {proc}() returns string language sql as
            $$ begin
                 system$log_info('harness-roundtrip {run_id}');
                 system$add_event('icecore.harness_probe', {{'icecore.run_id': '{run_id}'}});
                 return 'ok';
               end $$""")
        assert cur.execute(f"call {proc}()").fetchone()[0] == "ok"
        rows = wait_for_events(
            sf, "value::string ilike %(p)s or record_attributes:\"icecore.run_id\"::string = %(r)s",
            {"p": f"%{run_id}%", "r": run_id}, min_rows=2, since=since)
        assert {r["record_type"] for r in rows} >= {"LOG", "SPAN_EVENT"}
    finally:
        cur.execute(f"drop procedure if exists {proc}()")
        cur.close()
