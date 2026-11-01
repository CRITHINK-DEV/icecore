"""Spike S3: ingestion lag, concurrency and hook coverage.

Emission goes through the Gate 1 mechanism (spikes/GATE1.md, option B): a persisted SQL procedure in the
style of ICECORE_EMIT, here `ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_S3_<run_id>`, CALLed through the guard
block (`s1_emit_call`) and dropped at the end. The procedure stamps `icecore.emitted_at` with Snowflake's
clock (SYSDATE(), UTC) as it adds each event, and "first seen" is SYSDATE() of the poll query that first
returns the record, so the lag never mixes the laptop clock with Snowflake's (S4.md: the laptop runs
about 10.7 s ahead).

- Hook coverage: build, run, test, seed, snapshot and source freshness, each with `--vars {s3_probe: true}`,
  on the dbt version of the running venv. A command that does not fire a hook is a recorded gap.
- Concurrency: 20 separate dbt processes each run `s3_burst(run_id, 500)` (5 CALLs of 100 events, a CALL
  keeps only its last 128). Exactly 10,000 records must land for the run_id.
- Lag: per record, first seen minus emitted_at, from the same burst. p95 is compared with 60 s.

Results go to spikes/results/s3.json; the note is spikes/S3.md.
"""
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path

import pytest
import yaml
from dbt.version import __version__ as DBT_VERSION
from snowflake.connector import DictCursor

from helpers.events import EVENT_TABLE
from helpers.findings import record_finding
from helpers.macros import IT_DIR, invoke, run_macro

EMITTERS = 20
PER_EMITTER = 500
EXPECTED = EMITTERS * PER_EMITTER
POLL_S = 2
BURST_TIMEOUT_S = 900
HOOK_TIMEOUT_S = 240
LAG_BAR_S = 60

SELECT = ["--select", "tag:s3_spike"]
COMMANDS = {
    "build": ["build"] + SELECT,
    "run": ["run"] + SELECT,
    "test": ["test"] + SELECT,
    "seed": ["seed"] + SELECT,
    "snapshot": ["snapshot"] + SELECT,
    "source freshness": ["source", "freshness", "--select", "source:s3_spike"],
}
# One target directory per dbt version: 1.8 writes target/run/.../s3_snapshot.sql as a file where 1.12
# expects a directory, and 1.12 then fails the snapshot with "Not a directory".
TARGET = ["--target-path", str(IT_DIR / "target" / f"s3-dbt-{DBT_VERSION}")]
# What a command needs to exist before it runs; run without s3_probe, so it emits nothing.
PREREQS = {
    "test": [["run"] + SELECT],
    "source freshness": [["seed"] + SELECT],
}


# --- helpers -----------------------------------------------------------------------------------

def _proc(run_id: str) -> str:
    return f"ICECORE_SANDBOX.SPIKES.ICECORE_EMIT_S3_{run_id.upper()}"


def _sf_now(sf) -> dt.datetime:
    """Snowflake's clock, UTC (never the laptop's)."""
    return sf.cursor().execute("select sysdate()").fetchone()[0]


def _iso(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")


def _pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def _stats(lags: list[float]) -> dict:
    if not lags:
        return {"n": 0}
    return {"n": len(lags), "min_s": round(min(lags), 3), "p50_s": round(_pct(lags, 50), 3),
            "p95_s": round(_pct(lags, 95), 3), "p99_s": round(_pct(lags, 99), 3), "max_s": round(max(lags), 3)}


_POLL_SQL = (
    "select record_attributes:\"icecore.emitter\"::int as e, record_attributes:\"icecore.seq\"::int as s, "
    "record_attributes:\"icecore.phase\"::string as phase, record_attributes:\"icecore.emitted_at\"::string as em, "
    "record_attributes:\"dbt.command\"::string as cmd, record:\"name\"::string as name, timestamp as ts, "
    "sysdate() as now "
    f"from {EVENT_TABLE} "
    "where timestamp >= %(since)s and record_attributes:\"icecore.run_id\" = %(r)s"
)


class FirstSeen:
    """Polls the event table and keeps, per record key, the row and the Snowflake time of the poll that first saw it."""

    def __init__(self, sf, run_id: str, since: dt.datetime, key):
        self.sf, self.params, self.key = sf, {"r": run_id, "since": since}, key
        self.seen: dict = {}
        self.rows_last_poll = 0
        self.polls: list[float] = []  # monotonic start of every timed poll: only intervals are used

    def poll(self, timed: bool = True) -> int:
        """timed=False for a check after the measurement (e.g. the settle re-read): it can add records
        but is left out of the poll-interval statistic."""
        if timed:
            self.polls.append(time.monotonic())
        rows = self.sf.cursor(DictCursor).execute(_POLL_SQL, self.params).fetchall()
        self.rows_last_poll = len(rows)
        for r in rows:
            k = self.key(r)
            if k not in self.seen:
                self.seen[k] = {"row": r, "first_seen": r["NOW"]}
        return len(rows)

    def lags(self) -> list[float]:
        return [(v["first_seen"] - _iso(v["row"]["EM"])).total_seconds() for v in self.seen.values() if v["row"]["EM"]]

    def lags_vs_timestamp(self) -> list[float]:
        return [(v["first_seen"] - v["row"]["TS"]).total_seconds() for v in self.seen.values()]

    def visibility_batches(self) -> list[int]:
        """How many records each poll saw for the first time, in poll order (polls that saw none left out)."""
        return [n for _, n in sorted(Counter(v["first_seen"] for v in self.seen.values()).items())]

    def poll_interval_s(self) -> float | None:
        """Median time between the starts of consecutive timed polls: the resolution of first seen."""
        gaps = [b - a for a, b in zip(self.polls, self.polls[1:])]
        return round(_pct(gaps, 50), 2) if gaps else None


def _create_proc(run_id: str) -> None:
    res = invoke(["run-operation", "s3_emit_proc_create", "--args", json.dumps({"run_id": run_id})])
    assert res.success, f"could not create {_proc(run_id)}"


def _drop(sf, run_id: str) -> None:
    rid = run_id.upper()
    cur = sf.cursor()
    cur.execute(f"drop procedure if exists {_proc(run_id)}(ARRAY)")
    cur.execute(f"drop view if exists ICECORE_SANDBOX.SPIKES.S3_MODEL_{rid}")
    cur.execute(f"drop table if exists ICECORE_SANDBOX.SPIKES.S3_SEED_{rid}")
    cur.execute(f"drop table if exists ICECORE_SANDBOX.SPIKES.S3_SNAPSHOT_{rid}")


# --- offline: the probe is wired as the plan says, and inert for every other suite ---------------

def test_s3_burst_is_chunked_at_100_events_per_call():
    """A CALL keeps only its last 128 events (S2.md): 500 events are 5 CALLs of 100."""
    sizes = run_macro("s3_burst_plan", {"run_id": "r", "n": 500})
    assert sizes == [100] * 5
    assert run_macro("s3_burst_plan", {"run_id": "r", "n": 250}) == [100, 100, 50]


def test_s3_hooks_are_gated_by_the_s3_probe_var():
    cfg = yaml.safe_load(open(IT_DIR / "dbt_project.yml"))
    for key, phase in (("on-run-start", "start"), ("on-run-end", "end")):
        hooks = cfg[key] if isinstance(cfg[key], list) else [cfg[key]]
        # The UC1 hooks (icecore.start_run / end_run) sit next to the probe; only the probe is gated by s3_probe.
        assert [h for h in hooks if "s3_hook_probe" in h] == [
            f"{{{{ s3_hook_probe('{phase}') if var('s3_probe', false) else '' }}}}"]


def test_s3_resources_are_left_out_of_default_selection():
    tagged = invoke(["ls", "--select", "tag:s3_spike", "--resource-type", "all", "--output", "name"])
    assert tagged.success
    names = set(tagged.result)
    assert {"s3_model", "s3_seed", "s3_snapshot"} <= names and any("s3_spike" in n for n in names), names
    default = invoke(["ls", "--resource-type", "all", "--output", "name"])
    assert default.success
    assert not [n for n in default.result if "s3_" in n], default.result


# --- sandbox: hook coverage per command ------------------------------------------------------------

@pytest.mark.sandbox
@pytest.mark.parametrize("command", list(COMMANDS))
def test_s3_end_hook_fires(dbt, sf, run_id, command):
    _create_proc(run_id)
    try:
        for pre in PREREQS.get(command, []):
            assert invoke(pre + TARGET + ["--vars", json.dumps({"run_id": run_id})]).success, pre
        since = _sf_now(sf) - dt.timedelta(minutes=5)
        t0 = time.monotonic()
        res = invoke(COMMANDS[command] + TARGET + ["--vars", json.dumps({"s3_probe": True, "run_id": run_id})])
        wall_s = round(time.monotonic() - t0, 1)
        seen = FirstSeen(sf, run_id, since, key=lambda r: r["PHASE"])
        deadline = time.monotonic() + HOOK_TIMEOUT_S
        while len(seen.seen) < 2 and time.monotonic() < deadline:
            seen.poll()
            time.sleep(POLL_S)
        phases = sorted(seen.seen)
        finding = {
            "run_id": run_id, "dbt_version": DBT_VERSION, "command_ok": res.success, "dbt_wall_s": wall_s,
            "phases": phases, "start": "start" in seen.seen, "end": "end" in seen.seen,
            "dbt.command": sorted({v["row"]["CMD"] for v in seen.seen.values()}),
            "lag_s": {k: round((v["first_seen"] - _iso(v["row"]["EM"])).total_seconds(), 3) for k, v in seen.seen.items()},
            "waited_s": None if len(phases) == 2 else HOOK_TIMEOUT_S,
        }
        # S3_FINDING_SUFFIX labels a variant run, e.g. with a behaviour flag set in dbt_project.yml (S3.md)
        record_finding("s3", f"hooks.{DBT_VERSION}.{command}{os.environ.get('S3_FINDING_SUFFIX', '')}", finding)
    finally:
        _drop(sf, run_id)
    assert res.success, finding
    if phases != ["end", "start"]:
        pytest.xfail(f"gap recorded: dbt {DBT_VERSION} `{command}` fired {phases or 'no hook'} "
                     f"(nothing else landed in {HOOK_TIMEOUT_S}s)")


# --- sandbox: one 10,000-record burst, shared by the concurrency and the lag test --------------

_BURST_LINE = re.compile(r"icecore_s3_burst:(\{.*\})")


def _emitter_cmd(run_id: str, emitter: int, tmp: Path) -> list[str]:
    dbt_bin = str(Path(sys.executable).parent / "dbt")
    return [dbt_bin, "run-operation", "s3_burst",
            "--args", json.dumps({"run_id": run_id, "n": PER_EMITTER, "emitter": emitter}),
            "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR),
            "--target-path", str(tmp / f"target_{emitter}"), "--log-path", str(tmp / f"logs_{emitter}")]


def _server(sf, run_id: str) -> dict:
    """CALLs of the burst in query history: count, status, and any time blocked on a lock or queued."""
    rows = sf.cursor(DictCursor).execute(
        "select execution_status, total_elapsed_time, transaction_blocked_time, queued_overload_time "
        "from table(information_schema.query_history_by_user("
        "end_time_range_start => dateadd(minute, -60, current_timestamp()), result_limit => 10000)) "
        "where query_text ilike %(p)s and query_text ilike 'EXECUTE IMMEDIATE%%'",
        {"p": f"%ICECORE_EMIT_S3_{run_id.upper()}%"}).fetchall()
    ms = sorted(r["TOTAL_ELAPSED_TIME"] or 0 for r in rows)
    return {"calls": len(rows), "status": dict(Counter(r["EXECUTION_STATUS"] for r in rows)),
            "call_ms_p50": _pct(ms, 50) if ms else None, "call_ms_max": ms[-1] if ms else None,
            "transaction_blocked_ms": sum(r["TRANSACTION_BLOCKED_TIME"] or 0 for r in rows),
            "queued_overload_ms": sum(r["QUEUED_OVERLOAD_TIME"] or 0 for r in rows)}


@pytest.fixture(scope="module")
def burst(dbt, sf):
    run_id = uuid.uuid4().hex
    _create_proc(run_id)
    since = _sf_now(sf) - dt.timedelta(minutes=5)
    seen = FirstSeen(sf, run_id, since, key=lambda r: (r["E"], r["S"]))
    out = {"run_id": run_id, "dbt_version": DBT_VERSION, "emitters": EMITTERS, "per_emitter": PER_EMITTER}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            started = _sf_now(sf)
            procs = [subprocess.Popen(_emitter_cmd(run_id, i, Path(tmp)), stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
                     for i in range(1, EMITTERS + 1)]
            deadline = time.monotonic() + BURST_TIMEOUT_S
            done_at = None
            while time.monotonic() < deadline:
                seen.poll()
                if len(seen.seen) >= EXPECTED:
                    break
                if done_at is None and all(p.poll() is not None for p in procs):
                    done_at = _sf_now(sf)
                time.sleep(POLL_S)
            all_seen_at = _sf_now(sf)
            emitters = []
            for i, p in enumerate(procs, start=1):
                text = p.communicate(timeout=600)[0]
                m = _BURST_LINE.search(text)
                emitters.append({"emitter": i, "exit": p.returncode, **(json.loads(m.group(1)) if m else
                                                                         {"output_tail": text[-400:]})})
        time.sleep(60)  # a late duplicate or a late record would show up here
        seen.poll(timed=False)
        rows = sf.cursor().execute(
            f"select count(*), count(distinct record_attributes:\"icecore.emitter\"::int, "
            f"record_attributes:\"icecore.seq\"::int) from {EVENT_TABLE} "
            f"where timestamp >= %(since)s and record_attributes:\"icecore.run_id\" = %(r)s",
            {"since": since, "r": run_id}).fetchone()
        out.update({
            "records": rows[0], "distinct_records": rows[1], "expected": EXPECTED,
            "per_emitter_seen": dict(Counter(k[0] for k in seen.seen)),
            "emitters_ok": sum(1 for e in emitters if e["exit"] == 0),
            "call_statuses": dict(Counter(s for e in emitters for s in e.get("statuses", []))),
            "emitter_wall_s": sorted(e.get("wall_s") for e in emitters if e.get("wall_s") is not None),
            "seconds_to_first_seen": round((min(v["first_seen"] for v in seen.seen.values()) - started)
                                           .total_seconds(), 1) if seen.seen else None,
            "seconds_to_all_seen": round((all_seen_at - started).total_seconds(), 1),
            "seconds_emitters_done": round((done_at - started).total_seconds(), 1) if done_at else None,
            "poll_interval_s_p50": seen.poll_interval_s(),
            "polls": len(seen.polls),
            "visibility_batches": seen.visibility_batches(),
            "lag": _stats(seen.lags()),
            "lag_vs_event_timestamp": _stats(seen.lags_vs_timestamp()),
            "server": _server(sf, run_id),
        })
        bad = [e for e in emitters if e["exit"] != 0 or "statuses" not in e]
        if bad:
            out["failed_emitters"] = bad[:3]
        record_finding("s3", f"burst.{DBT_VERSION}", out)
    finally:
        _drop(sf, run_id)
    return out


@pytest.mark.sandbox
def test_s3_no_lost_records_under_concurrency(burst):
    assert burst["emitters_ok"] == EMITTERS, burst.get("failed_emitters")
    assert burst["records"] == EXPECTED and burst["distinct_records"] == EXPECTED, \
        {k: burst[k] for k in ("records", "distinct_records", "per_emitter_seen")}
    assert burst["server"]["transaction_blocked_ms"] == 0, burst["server"]


@pytest.mark.sandbox
def test_s3_lag_p95_under_60s(burst):
    lag = burst["lag"]
    assert lag["n"] > 0, burst
    assert lag["p95_s"] < LAG_BAR_S, lag
