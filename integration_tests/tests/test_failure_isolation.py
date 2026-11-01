"""UC1 failure isolation: a telemetry failure never breaks the user's run (macros/telemetry/emit.sql, end_run.sql).

dbt's Jinja has no try/except, so a Jinja exception inside a macro cannot be caught. Isolation is built from what
can be: (a) every query an emitter makes runs inside a Snowflake Scripting guard (`exception when other`) and
comes back as a status; (b) emitters only build data and validate their inputs (none, wrong types), so they do not
raise; (c) in end_run's loop an emitter that returns an error does not stop the next one. A Jinja-level exception
in an emitter would still stop the hook.

Offline tests render through integration_tests/macros/test_support/it_isolation.sql. Sandbox tests use an obs
schema of their own (procedures from icecore.create_emit_procedures) and, for the missing-association case, a schema
that does not exist: the shared sandbox database is never altered.
"""
import json
import os
import uuid

import pytest

from helpers.events import wait_for_events
from helpers.macros import IT_DIR, invoke, run_macro, run_macro_with_logs
from helpers.run_hooks import (
    BASE_FILTER, drop_fixture_views, icecore_vars, obs_schema, record_params, snowflake_since,
)

KB = 1024
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
WARNING = "icecore: warning:"


def with_vars(settings: dict, **top) -> list[str]:
    return ["--vars", json.dumps({"icecore": settings, **top})]


def result(unique_id: str, status: str, resource_type: str = "model") -> dict:
    name = unique_id.split(".")[-1]
    return {"unique_id": unique_id, "status": status, "timing": [], "execution_time": 1.5, "adapter_response": {},
            "message": None, "failures": None,
            "node": {"unique_id": unique_id, "name": name, "resource_type": resource_type,
                     "config": {"materialized": "view"}}}


RESULTS = [result("model.p.a", "success"), result("test.p.t", "pass", "test")]


def kinds(items: list[dict]) -> list:
    return [i.get("attrs", {}).get("icecore.kind") or i.get("name") for i in items]


# --- offline: rendering only, no connection --------------------------------------------------------

def test_wrong_types_are_skipped_or_coerced_never_raised():
    events = [None, "text", 5, ["list"], {"kind": 5, "attributes": "bad", "message": 3, "level": None},
              {"kind": None, "attributes": {7: "int key", "ok": 1, "obj": {"nested": [1, "a"]}}}]
    out = run_macro("it_emit_render", {"events": events})
    first, second = out["records"]
    assert len(out["records"]) == 2
    assert first["name"] == "icecore.5" and first["attrs"]["icecore.message"] == "3"
    assert first["attrs"]["icecore.level"] == "info" and "bad" not in first["attrs"].values()
    assert second["name"] == "icecore.event"
    assert second["attrs"]["ok"] == 1 and second["attrs"]["7"] == "int key"
    assert second["attrs"]["obj"] == '{"nested": [1, "a"]}'  # nested values are JSON text on the ICECORE_EMIT path


@pytest.mark.parametrize("events", [None, [], "text", 5, [None], ["x", 3]])
def test_emit_events_with_nothing_usable_sends_nothing(events):
    # Offline any query would fail to connect: success proves none was run.
    out = run_macro("it_emit_events", {"events": events})
    assert out["calls"] == 0 and out["statuses"] == [] and out["events"] == 0


@pytest.mark.parametrize("results", [None, "junk", 5, {"a": 1}])
def test_end_run_survives_odd_results(results):
    end = run_macro("it_end_run_isolation", {"results": results, "emitters": []})["items"][-1]
    assert end["attrs"]["icecore.kind"] == "run_end" and end["attrs"]["dbt.invocation.nodes_total"] == 0


def test_end_run_survives_junk_inside_the_results():
    items = run_macro("it_end_run_isolation", {"results": [None, "x", 3] + RESULTS, "emitters": []})["items"]
    assert items[-1]["attrs"]["icecore.kind"] == "run_end"
    assert kinds(items).count("node_result") == 2


def test_an_emitter_that_returns_an_error_does_not_stop_the_next():
    out, logs = run_macro_with_logs("it_end_run_isolation", {"results": RESULTS, "emitters": ["error", "marker"]})
    assert "it_marker" in kinds(out["items"])
    assert kinds(out["items"])[-1] == "run_end"
    warnings = [line for line in logs if line.startswith(WARNING)]
    assert len(warnings) == 1 and "it_error" in warnings[0] and "simulated" in warnings[0], warnings


def test_a_broken_emitter_entry_is_skipped_with_one_warning():
    out, logs = run_macro_with_logs("it_end_run_isolation",
                                    {"results": RESULTS, "emitters": ["not_callable", "malformed", "marker"]})
    assert "it_marker" in kinds(out["items"]) and kinds(out["items"])[-1] == "run_end"
    warnings = [line for line in logs if line.startswith(WARNING)]
    assert len(warnings) == 1 and "not_callable" in warnings[0], warnings


def test_the_packages_own_emitters_still_run_before_the_extras():
    out = run_macro("it_end_run_isolation", {"results": RESULTS, "emitters": ["error", "marker"]})
    k = kinds(out["items"])
    assert k[0] == "icecore.invocation" or k[0].startswith("icecore.")
    assert k.count("node_result") == 2 and k.index("it_marker") > k.index("node_result")


def test_chunks_measure_the_escaped_literal_not_the_raw_json():
    # `$` is sent as \x24 (four characters) and `'` as \' (two): the raw JSON of these records is far smaller.
    events = [{"kind": "m", "attributes": {"v": "$" * (60 * KB), "w": "'" * (20 * KB)}} for _ in range(12)]
    chunks = run_macro("it_chunk_literals", {"events": events, "max_bytes": 512 * KB})
    assert sum(c["records"] for c in chunks) == 12 and len(chunks) >= 3
    assert all(c["literal_bytes"] <= 512 * KB for c in chunks), chunks


def test_emit_writes_nested_values_as_json_text_and_emit_run_keeps_them_native():
    attrs = {"a": {"k": [1, {"d": "x"}]}, "b": [1, 2], "c": "text", "d": 5}
    emit_rec = run_macro("it_emit_render", {"events": [{"kind": "k", "attributes": attrs}]})["records"][0]["attrs"]
    assert emit_rec["a"] == '{"k": [1, {"d": "x"}]}' and emit_rec["b"] == "[1, 2]" and emit_rec["c"] == "text" and emit_rec["d"] == 5
    run_rec = run_macro("it_emit_run_render", {"items": [{"type": "log", "message": "m", "attrs": attrs}]})["records"][0]["attrs"]
    assert run_rec["a"] == {"k": [1, {"d": "x"}]} and run_rec["b"] == [1, 2]


def test_dates_are_iso_and_nested_values_are_cleaned_recursively():
    a = run_macro("it_safe_attributes", {})
    assert a["date"] == "2026-11-01" and a["datetime"] == "2026-11-01T10:30:05.123456"
    assert a["nested"] == {"when": "2026-11-01T10:30:05.123456",
                           "list": ["2026-11-01", 1.5, {"deep": "2026-11-01T10:30:05.123456"}], "n": 7}
    assert a["list"] == ["2026-11-01", ["2026-11-01T10:30:05.123456"]]
    assert a["bool"] is True and a["none"] is None and a["int"] == 3 and a["float"] == 2.5
    assert isinstance(a["obj"], str)  # anything unknown is its text, never an exception


# --- sandbox ---------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


def run_with_warnings(args: list[str], run_id: str, schema: str, **settings):
    """dbt <args> with the hooks writing to schema; returns (result, the `icecore: warning:` lines)."""
    target = IT_DIR / "target" / f"isolation-{run_id}-{uuid.uuid4().hex[:8]}"
    warnings = []

    def collect(event) -> None:
        msg = getattr(event.info, "msg", "") or ""
        if event.info.name == "JinjaLogInfo" and msg.startswith(WARNING):
            warnings.append(msg)

    dbt_vars = {"run_id": run_id, **icecore_vars(schema, **settings)}
    try:
        res = invoke(args + ["--target-path", str(target), "--vars", json.dumps(dbt_vars)], [collect])
    finally:
        import shutil
        shutil.rmtree(target, ignore_errors=True)
    return res, warnings


@pytest.mark.sandbox
def test_build_with_no_emitter_procedures_succeeds_with_one_warning_per_hook(dbt, sf, run_id):
    # The obs schema does not exist: the same failure as an event table association removed (2141), without
    # touching the shared database.
    try:
        res, warnings = run_with_warnings(["build", "--select", "hooks_a+"], run_id, f"ICECORE_OBS_MISSING_{run_id}")
        assert res.success, res.exception
        assert len(warnings) == 2, warnings
        assert sum("ICECORE_EMIT_RUN" in w for w in warnings) == 1
        assert sum("ICECORE_EMIT\"" in w for w in warnings) == 1
        assert all("telemetry not written" in w for w in warnings)
    finally:
        drop_fixture_views(sf, run_id)


@pytest.mark.sandbox
def test_many_failing_chunks_log_one_warning_for_the_hook(dbt, run_id):
    events = [{"kind": "m", "attributes": {"v": "x" * (40 * KB), "i": i}} for i in range(40)]  # several CALLs
    out, logs = run_macro_with_logs("it_emit_events", {"events": events},
                                    with_vars({"obs_schema": f"ICECORE_OBS_MISSING_{run_id}"}))
    assert out["calls"] >= 3 and all(s.startswith("icecore-error") for s in out["statuses"]), out
    warnings = [line for line in logs if line.startswith(WARNING)]
    assert len(warnings) == 1 and f"{out['calls']} of {out['calls']} CALLs" in warnings[0], warnings


@pytest.mark.sandbox
def test_a_procedure_the_role_cannot_use_returns_a_status_and_a_warning(dbt):
    # GATE1.md proc.no_usage_guarded: no USAGE on SNOWFLAKE.TELEMETRY, the same 2141 as a missing procedure.
    out, logs = run_macro_with_logs("it_guarded_call", {"procedure": 'SNOWFLAKE.TELEMETRY."ICECORE_EMIT"', "items": []})
    assert out.startswith("icecore-error "), out
    assert len([line for line in logs if line.startswith(WARNING)]) == 1


@pytest.mark.sandbox
def test_one_emitter_whose_guarded_query_fails_does_not_stop_the_others(sf, obs, run_id):
    since = snowflake_since(sf)
    out, logs = run_macro_with_logs(
        "it_end_run_isolation_send", {"results": RESULTS, "emitters": ["guarded_failure", "marker"]},
        ["--vars", json.dumps({"it_run_id": run_id, **icecore_vars(obs)})])
    assert out["calls"] == 1 and out["statuses"][0].startswith("ok"), out
    assert any("it_guarded_failure" in line for line in logs if line.startswith(WARNING)), logs
    rows = wait_for_events(sf, "record_attributes:\"it.run_id\"::string = %(tag)s and "
                               "resource_attributes:\"snow.schema.name\"::string = %(schema)s",
                           {"tag": run_id, "schema": obs}, since=since)
    assert [r["record_attributes"]["icecore.kind"] for r in rows] == ["it_marker"]


@pytest.mark.sandbox
def test_run_query_values_decimal_dates_and_nested_land_as_json(sf, obs, run_id):
    # run_query returns Decimal for numbers: json cannot serialise it, which used to fail the hook.
    since = snowflake_since(sf)
    out, _ = run_macro_with_logs("it_emit_query_values", {"run_id": run_id},
                                 ["--vars", json.dumps(icecore_vars(obs))])
    assert out["emit"]["statuses"] == ["ok 1"], out
    assert out["types"][0] == "1.50"
    rows = wait_for_events(sf, "record_attributes:\"it.run_id\"::string = %(tag)s and "
                               "resource_attributes:\"snow.schema.name\"::string = %(schema)s",
                           {"tag": run_id, "schema": obs}, since=since)
    a = rows[0]["record_attributes"]
    assert a["it.decimal"] == 1.5 and a["it.int"] == 7
    assert a["it.date"] == out["types"][1] and a["it.ts"].startswith(out["types"][2][:10])
    # SYSTEM$ADD_EVENT silently drops object and list attributes (measured 1 Nov 2026): emit sends them as JSON text.
    # emit writes a mapping or list value as JSON text, so it lands instead of being dropped
    assert isinstance(a["it.nested"], str) and isinstance(a["it.list"], str)
    nested, items = json.loads(a["it.nested"]), json.loads(a["it.list"])
    assert nested["dec"] == 1.5 and nested["when"] == a["it.ts"] and nested["list"][0] == 1.5
    assert items[0] == 1.5 and items[1] == {"ts": a["it.ts"]}


def epoch_ns(sf) -> int:
    return int(sf.cursor().execute("select date_part(epoch_nanosecond, current_timestamp())").fetchone()[0])


@pytest.mark.sandbox
def test_records_survive_a_rollback_through_both_procedures(sf, obs, run_id):
    """ICECORE_EMIT was measured in S1 (proc.rollback); ICECORE_EMIT_RUN is GATE1.md open item 3. Both CALLs run
    inside one explicit transaction that is rolled back; the control table proves the rollback happened."""
    since = snowflake_since(sf)
    cur = sf.cursor()
    q = f'"{DATABASE}"."{obs}"'
    now = epoch_ns(sf)
    events = [{"name": "icecore.it_rollback", "attrs": {"it.run_id": run_id, "dbt.invocation_id": run_id, "it.seq": 1}}]
    spans = [{"name": "icecore.node", "start_ns": now - 3 * 10**9, "end_ns": now - 2 * 10**9,
              "attrs": {"it.run_id": run_id, "dbt.invocation_id": run_id}, "events": []},
             {"type": "log", "level": "info", "message": "it rollback",
              "attrs": {"it.run_id": run_id, "dbt.invocation_id": run_id, "icecore.kind": "it_rollback"}}]
    cur.execute(f"create temporary table it_rb_{run_id} (a int)")
    cur.execute("begin")
    cur.execute(f"insert into it_rb_{run_id} values (1)")
    emit = cur.execute(f'call {q}."ICECORE_EMIT"(parse_json(%s))', (json.dumps(events),)).fetchone()[0]
    emit_run = cur.execute(f'call {q}."ICECORE_EMIT_RUN"(parse_json(%s))', (json.dumps(spans),)).fetchone()[0]
    assert cur.execute(f"select count(*) from it_rb_{run_id}").fetchone()[0] == 1
    cur.execute("rollback")
    assert cur.execute(f"select count(*) from it_rb_{run_id}").fetchone()[0] == 0  # the rollback happened
    assert emit == "ok 1" and emit_run == "ok spans=1 logs=1", (emit, emit_run)
    rows = wait_for_events(sf, BASE_FILTER, record_params(obs, run_id), min_rows=3, since=since, settle_s=10)
    by_type = sorted(r["record_type"] for r in rows)
    assert by_type == ["LOG", "SPAN", "SPAN_EVENT"], by_type
