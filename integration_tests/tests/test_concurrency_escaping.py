"""UC1 concurrency and escaping scenarios, through the real write path (start_run, end_run, emit, emit_run).

- A run of 200 nodes on 8 dbt threads lands 200 node results, 200 node spans and 200 query events, counted by
  dbt.invocation_id, with none lost.
- Two dbt invocations at the same time against one obs schema each land all their records, and none is mixed.
- Messages and attribute values with quotes, backslashes, `$$`, newlines, non-ASCII and emoji round-trip byte for
  byte, through emit and emit_run, and a failing model's Snowflake error message (which quotes a value with a quote
  and a backslash) lands identical to dbt's.
- A value at the payload cap is kept whole, one byte over is cut and flagged, and one item larger than the 512 KB
  chunk (escaped) is sent alone and lands.

Sandbox only, in an obs schema of its own; small volumes (S3 already measured 10,000 records). The 200 models are
generated at test time into their own schema and dropped.
"""
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from helpers.events import wait_for_events
from helpers.macros import IT_DIR, run_macro, run_macro_with_logs
from helpers.run_hooks import (
    BASE_FILTER, DATABASE, icecore_vars, invocation_records, node_results, obs_schema, record_params, run_end_logs,
    run_with_hooks, snowflake_since,
)

KB = 1024
WEIRD = ("it's a \\ test \\n literal \"double\" $$ end $ of $block? $$\nnew line\r\ncrlf\ttab "
         "é ü ñ 漢字   emoji 😀🚀 \\x24 ''")
SCALE = 200


def with_vars(settings: dict, **top) -> list[str]:
    return ["--vars", json.dumps({"icecore": settings, **top})]


@pytest.fixture(scope="module")
def obs(dbt, sf):
    with obs_schema(sf) as schema:
        yield schema


def tagged(obs, run_id) -> str:
    return ("resource_attributes:\"snow.database.name\"::string = %(db)s"
            " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
            " and startswith(resource_attributes:\"snow.executable.name\"::string, 'ICECORE_EMIT')"
            " and record_attributes:\"it.run_id\"::string = %(tag)s"), {"db": DATABASE, "schema": obs, "tag": run_id}


def kind_of(row: dict):
    return row["record_attributes"].get("icecore.kind")


# --- parallel threads: 200 nodes on 8 threads ----------------------------------------------------------

@pytest.fixture
def scale_project(sf, run_id):
    """200 one-line views in a schema of their own, generated under models/ and removed afterwards."""
    folder = IT_DIR / "models" / f"scale_{run_id}"
    folder.mkdir()
    for i in range(SCALE):
        (folder / f"scale_{i:03d}.sql").write_text(
            f"{{{{ config(materialized='view', schema='scale_{run_id}') }}}}\nselect {i} as id\n")
    yield folder
    shutil.rmtree(folder, ignore_errors=True)
    sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES").upper()}_SCALE_{run_id.upper()}" cascade')


@pytest.mark.sandbox
def test_200_nodes_on_8_threads_land_200_node_results_and_spans(sf, obs, run_id, scale_project):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", f"path:models/scale_{run_id}", "--threads", "8"],
                                      run_id, obs)
    assert res.success, res.exception
    assert len(node_results(run_results)) == SCALE
    invocation_id = run_results["metadata"]["invocation_id"]
    # 1 start + (1 invocation span + 200 node spans + 200 query events + 200 node results + 1 end)
    rows = invocation_records(sf, obs, invocation_id, min_rows=603, since=since, settle_s=30)
    by = {}
    for r in rows:
        key = (r["record_type"], kind_of(r) or r["record"].get("name"))
        by[key] = by.get(key, 0) + 1
    assert by[("LOG", "node_result")] == SCALE, by
    assert by[("SPAN", "icecore.node")] == SCALE, by
    assert by[("SPAN_EVENT", "icecore.query")] == SCALE, by
    unique = {r["record_attributes"]["dbt.node.unique_id"] for r in rows if kind_of(r) == "node_result"}
    assert len(unique) == SCALE  # none duplicated, none lost
    assert len(run_end_logs(rows)) == 1


# --- two invocations at the same time ------------------------------------------------------------------

def start_cli(args: list[str], run_id: str, schema: str, tag: str):
    """`dbt <args>` as a subprocess (dbt's runner is not thread-safe in one process), with its own target path."""
    target = IT_DIR / "target" / f"concurrent-{tag}-{uuid.uuid4().hex[:8]}"
    dbt_vars = {"run_id": run_id, **icecore_vars(schema)}
    cmd = [str(Path(sys.executable).parent / "dbt")] + args + [
        "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR), "--target-path", str(target),
        "--log-path", str(target / "logs"), "--vars", json.dumps(dbt_vars)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True), target


@pytest.mark.sandbox
def test_two_invocations_at_once_each_land_all_their_records_and_none_is_mixed(sf, obs, run_id):
    since = snowflake_since(sf)
    # A builds hooks_a and its chain (a, b, c and c's test); B builds only hooks_fail's sibling hooks_msg.
    (pa, ta), (pb, tb) = (start_cli(["build", "--select", "hooks_a+"], f"{run_id}a", obs, "a"),
                          start_cli(["build", "--select", "hooks_msg"], f"{run_id}b", obs, "b"))
    out_a, out_b = pa.communicate(timeout=600)[0], pb.communicate(timeout=600)[0]
    try:
        assert pa.returncode == 0, out_a[-2000:]
        assert pb.returncode == 0, out_b[-2000:]
        runs = {k: json.load(open(t / "run_results.json")) for k, t in (("a", ta), ("b", tb))}
    finally:
        for t in (ta, tb):
            shutil.rmtree(t, ignore_errors=True)
        for suffix in ("a", "b"):
            for m in ("hooks_a", "hooks_b", "hooks_c", "hooks_msg"):
                sf.cursor().execute(f'drop {"table" if m == "hooks_msg" else "view"} if exists "{DATABASE}"."{os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES").upper()}"."{m.upper()}_{run_id.upper()}{suffix.upper()}"')
    ids = {k: v["metadata"]["invocation_id"] for k, v in runs.items()}
    assert ids["a"] != ids["b"]
    expected = {k: sorted(r["unique_id"] for r in node_results(v)) for k, v in runs.items()}
    assert len(expected["a"]) > len(expected["b"]) == 1
    for k in ("a", "b"):
        rows = invocation_records(sf, obs, ids[k], min_rows=len(expected[k]) * 2 + 3, since=since, settle_s=20)
        landed = sorted(r["record_attributes"]["dbt.node.unique_id"] for r in rows if kind_of(r) == "node_result")
        assert landed == expected[k], k  # all its node results, and none of the other run's
        spans = sorted(r["record_attributes"]["dbt.node.unique_id"] for r in rows
                       if r["record_type"] == "SPAN" and r["record"]["name"] == "icecore.node")
        assert spans == expected[k], k
        assert len(run_end_logs(rows)) == 1
        assert {r["record_attributes"]["dbt.invocation_id"] for r in rows} == {ids[k]}


# --- escaping round trip ------------------------------------------------------------------------------

@pytest.mark.sandbox
def test_emit_round_trips_special_characters_byte_for_byte(sf, obs, run_id, monkeypatch):
    since = snowflake_since(sf)
    monkeypatch.setenv("IT_WEIRD", WEIRD)
    out = run_macro("it_emit_weird", {"run_id": run_id}, with_vars({"obs_schema": obs}))
    assert out["statuses"] == ["ok 1"], out
    where, params = tagged(obs, run_id)
    a = wait_for_events(sf, where, params, since=since)[0]["record_attributes"]
    # SYSTEM$ADD_EVENT drops object values, so emit sends a mapping as JSON text: it lands, and parses back.
    assert json.loads(a["it.nested"]) == {"k": WEIRD}
    assert a["icecore.message"] == WEIRD and a["it.value"] == WEIRD
    assert a["icecore.message"].encode() == WEIRD.encode()


@pytest.mark.sandbox
def test_emit_run_round_trips_special_characters_byte_for_byte(sf, obs, run_id, monkeypatch):
    since = snowflake_since(sf)
    monkeypatch.setenv("IT_WEIRD", WEIRD)
    now = int(sf.cursor().execute("select date_part(epoch_nanosecond, current_timestamp())").fetchone()[0])
    out = run_macro("it_emit_run_weird", {"run_id": run_id, "start_ns": now}, with_vars({"obs_schema": obs}))
    assert out["statuses"] == ["ok spans=1 logs=1"], out
    where, params = tagged(obs, run_id)
    rows = wait_for_events(sf, where, params, min_rows=3, since=since, settle_s=5)
    assert len(rows) == 3
    for r in rows:
        assert r["record_attributes"]["it.value"] == WEIRD, r["record_type"]
    log = next(r for r in rows if r["record_type"] == "LOG")
    assert log["value"] == WEIRD and log["value"].encode() == WEIRD.encode()


@pytest.mark.sandbox
def test_a_failing_models_error_message_lands_identical(sf, obs, run_id):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_msg"], run_id, obs,
                                      extra_vars={"hooks_msg": True}, result_message="full")
    try:
        assert not res.success
        message = node_results(run_results)[0]["message"]
        assert "'it's a \\ test'" in message, message  # the quote and the backslash are in dbt's message
        rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=3, since=since)
        landed = next(r for r in rows if kind_of(r) == "node_result")
        assert landed["record_attributes"]["dbt.result.message"] == message
        assert landed["value"] == "model.icecore_integration_tests.hooks_msg error: " + message
    finally:
        sf.cursor().execute(f'drop table if exists "{DATABASE}"."{os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES").upper()}"."HOOKS_MSG_{run_id.upper()}"')


@pytest.mark.sandbox
def test_run_query_decimals_dates_and_nested_values_land_through_both_procedures(sf, obs, run_id):
    since = snowflake_since(sf)
    out, _ = run_macro_with_logs("it_emit_query_values", {"run_id": run_id}, with_vars({"obs_schema": obs}))
    assert out["emit"]["statuses"] == ["ok 1"], out
    now = int(sf.cursor().execute("select date_part(epoch_nanosecond, current_timestamp())").fetchone()[0])
    out, _ = run_macro_with_logs("it_emit_run_query_values", {"run_id": run_id + "r", "start_ns": now},
                                 with_vars({"obs_schema": obs}))
    assert out["emit_run"]["statuses"] == ["ok spans=1 logs=1"], out
    where, params = tagged(obs, run_id)
    emit_row = wait_for_events(sf, where, params, since=since)[0]["record_attributes"]
    where, params = tagged(obs, run_id + "r")
    rows = wait_for_events(sf, where, params, min_rows=3, since=since, settle_s=5)
    assert len(rows) == 3
    # scalars: the Decimal is a number, the date and the timestamp are ISO text
    for a in [emit_row] + [r["record_attributes"] for r in rows]:
        assert a["it.decimal"] == 1.5
    assert emit_row["it.date"]
    # nested values were serialisable, so nothing failed; what the procedure keeps of them is measured here
    kept = {r["record_type"]: {k: v for k, v in r["record_attributes"].items() if k in ("it.nested", "it.list")}
            for r in rows}
    assert json.loads(emit_row["it.nested"])["dec"] == 1.5 and json.loads(emit_row["it.list"])[0] == 1.5  # JSON text
    # Finding (1 Nov 2026): through ICECORE_EMIT_RUN a LOG keeps a nested mapping and list (Decimal as a number, dates
    # as ISO text); a SPAN and a SPAN_EVENT silently drop them, (ICECORE_EMIT sends nested values as JSON text for that reason).
    log = kept["LOG"]
    assert log["it.list"][0] == 1.5 and log["it.nested"]["dec"] == 1.5
    assert log["it.nested"]["when"][:4].isdigit() and "T" in log["it.nested"]["when"]
    assert kept["SPAN"] == {} and kept["SPAN_EVENT"] == {}


# --- payload cap and chunk size ------------------------------------------------------------------------

def cap_overhead(run_id: str, cap_kb: int) -> int:
    """Bytes of the record's attribute JSON without the value `it.v`."""
    record = run_macro("it_emit_render", {"events": [{"kind": "it_cap", "attributes": {"it.run_id": run_id, "it.v": ""}}]},
                       with_vars({"payload_cap_kb": cap_kb}))["records"][0]
    return len(json.dumps(record["attrs"]))


@pytest.mark.sandbox
def test_value_at_the_cap_is_whole_and_one_byte_over_is_cut_and_flagged(sf, obs, run_id):
    since = snowflake_since(sf)
    cap = 64 * KB
    at_cap = cap - cap_overhead(run_id + "at", 64)
    over = cap - cap_overhead(run_id + "over", 64) + 1  # the tag differs in length, so the overhead is measured per case
    for length, tag in ((at_cap, "at"), (over, "over")):
        out = run_macro("it_emit", {"kind": "it_cap", "attributes": {"it.run_id": run_id + tag, "it.v": "v" * length}},
                        with_vars({"obs_schema": obs}))
        assert out["statuses"] == ["ok 1"], out
    where_at, p_at = tagged(obs, run_id + "at")
    where_over, p_over = tagged(obs, run_id + "over")
    a = wait_for_events(sf, where_at, p_at, since=since)[0]["record_attributes"]
    assert a["it.v"] == "v" * at_cap and "icecore.truncated" not in a
    b = wait_for_events(sf, where_over, p_over, since=since)[0]["record_attributes"]
    assert b["icecore.truncated"] is True and b["icecore.truncated_keys"] == "it.v"
    assert 0 < len(b["it.v"]) < over and set(b["it.v"]) == {"v"}


@pytest.mark.sandbox
def test_items_near_and_over_the_512kb_chunk_land_whole_apart_from_the_cap(sf, obs, run_id):
    since = snowflake_since(sf)
    # 300 KB of `$` is cut to the 256 KB cap; two of them are over one 512 KB chunk, and each is about 1 MB once
    # escaped (`$` is sent as \x24), so the statements are far larger than the chunk size.
    huge = "$" * (300 * KB)
    events = [{"kind": "it_chunk", "attributes": {"it.run_id": run_id, "it.n": 1}},
              {"kind": "it_chunk", "attributes": {"it.run_id": run_id, "it.n": 2, "it.v": huge}},
              {"kind": "it_chunk", "attributes": {"it.run_id": run_id, "it.n": 3, "it.v": huge}},
              {"kind": "it_chunk", "attributes": {"it.run_id": run_id, "it.n": 4}}]
    out = run_macro("it_emit_events", {"events": events}, with_vars({"obs_schema": obs, "payload_cap_kb": 256}))
    assert out["calls"] >= 2 and all(s.startswith("ok ") for s in out["statuses"]), out
    where, params = tagged(obs, run_id)
    rows = wait_for_events(sf, where, params, min_rows=4, since=since, settle_s=5)
    assert sorted(r["record_attributes"]["it.n"] for r in rows) == [1, 2, 3, 4]
    for r in rows:
        a = r["record_attributes"]
        if a["it.n"] in (2, 3):
            assert a["icecore.truncated"] is True and set(a["it.v"]) == {"$"} and len(a["it.v"]) > 200 * KB

    # the same through emit_run: LOG messages larger than a chunk once escaped
    items = [{"type": "log", "message": "small", "attrs": {"it.run_id": run_id + "r", "it.n": 1}},
             {"type": "log", "message": huge, "attrs": {"it.run_id": run_id + "r", "it.n": 2}},
             {"type": "log", "message": huge, "attrs": {"it.run_id": run_id + "r", "it.n": 3}},
             {"type": "log", "message": "small", "attrs": {"it.run_id": run_id + "r", "it.n": 4}}]
    out = run_macro("it_emit_run_full", {"items": items}, with_vars({"obs_schema": obs, "payload_cap_kb": 256}))
    assert out["calls"] >= 2 and all(s.startswith("ok spans=0 logs=") for s in out["statuses"]), out
    where, params = tagged(obs, run_id + "r")
    rows = wait_for_events(sf, where, params, min_rows=4, since=since, settle_s=5)
    assert sorted(r["record_attributes"]["it.n"] for r in rows) == [1, 2, 3, 4]
    for r in rows:
        if r["record_attributes"]["it.n"] in (2, 3):
            assert set(r["value"]) == {"$"} and len(r["value"]) > 200 * KB
            assert r["record_attributes"]["icecore.truncated"] is True
