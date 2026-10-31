"""UC1 run hooks: icecore.start_run (macros/telemetry/start_run.sql), icecore.end_run (end_run.sql) and the run
emitter they share, icecore.emit_run (emit_run.sql). Gate 1 option B (spikes/GATE1.md).

start_run writes one span event `icecore.run_start` through ICECORE_EMIT. end_run builds one batch (the
emitters behind their vars, then the invocation-end LOG with status and node counts) and sends it to
ICECORE_EMIT_RUN in as many guarded CALLs of at most 512 KB as needed. Both are no-ops when
var('icecore').telemetry is false.

Offline tests render through integration_tests/macros/test_support/it_hooks.sql. Sandbox tests run real
`dbt build`s of models/hooks against an obs schema of their own (helpers/run_hooks.py).
"""
import json

import pytest
import yaml

from helpers.macros import IT_DIR, run_macro
from helpers.run_hooks import (
    assert_no_invocation_records, drop_fixture_views, invocation_records, node_results, obs_schema, run_end_logs,
    run_start_events, run_with_hooks, snowflake_since,
)

REPO = IT_DIR.parent
KB = 1024


def with_vars(icecore_vars: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": icecore_vars})]


def result(unique_id: str, status: str, resource_type: str = "model", **extra) -> dict:
    """A run result as on-run-end sees it (the shape of run_results.json), with its node."""
    name = unique_id.split(".")[-1]
    return {"unique_id": unique_id, "status": status, "timing": [], "execution_time": 1.5,
            "adapter_response": {}, "message": None, "failures": None,
            "node": {"unique_id": unique_id, "name": name, "resource_type": resource_type,
                     "config": {"materialized": "view"}}, **extra}


SUCCESS = [result("model.p.a", "success"), result("model.p.b", "success"),
           result("test.p.t", "pass", "test")]
MIXED = [result("operation.p.p-on-run-start-0", "success", "operation"), result("model.p.a", "success"), result("model.p.bad", "error"), result("model.p.after", "skipped"),
         result("test.p.t", "warn", "test"), result("test.p.u", "pass", "test")]


# --- offline: rendering only, no connection ------------------------------------------------------

def test_start_record_carries_dbt_version_and_engines():
    record, = run_macro("it_run_start_record", {})
    assert record["name"] == "icecore.run_start"
    a = record["attrs"]
    assert a["dbt.invocation_id"] and a["dbt.command"] == "run-operation"
    assert a["dbt.version"].split(".")[0].isdigit()
    assert a["icecore.anomaly_engine"] == "auto" and a["icecore.shadow_engine"] == "none"
    assert a["icecore.message"] == "dbt run-operation started"


def test_start_record_follows_the_engine_vars():
    record, = run_macro("it_run_start_record", {}, with_vars({"anomaly_engine": "cortex", "shadow_engine": "native"}))
    assert record["attrs"]["icecore.anomaly_engine"] == "cortex"
    assert record["attrs"]["icecore.shadow_engine"] == "native"


@pytest.mark.parametrize("off", [False, "false"])
def test_hooks_are_noops_when_telemetry_is_false(off):
    # Offline, any query would fail to connect: success proves neither hook ran one.
    out = run_macro("it_hooks_render", {"results": MIXED}, with_vars({"telemetry": off}))
    assert out == {"start_run": "", "end_run": "", "telemetry": False}


def test_run_end_log_has_status_and_node_counts():
    records = run_macro("it_end_run_render", {"results": SUCCESS})["records"]
    end = records[-1]
    assert end["type"] == "log" and end["level"] == "info"
    a = end["attrs"]
    assert a["icecore.kind"] == "run_end"
    assert a["dbt.invocation.status"] == "success"
    assert (a["dbt.invocation.nodes_total"], a["dbt.invocation.nodes_success"], a["dbt.invocation.nodes_failed"],
            a["dbt.invocation.nodes_warned"], a["dbt.invocation.nodes_skipped"]) == (3, 3, 0, 0, 0)
    assert a["dbt.invocation_id"] and a["dbt.version"]
    assert end["message"] == "dbt run-operation finished: success, 3 nodes (3 succeeded, 0 failed, 0 warned, 0 skipped)"


def test_a_failed_node_makes_the_run_status_error():
    end = run_macro("it_end_run_render", {"results": MIXED})["records"][-1]
    a = end["attrs"]
    assert end["level"] == "error"
    assert a["dbt.invocation.status"] == "error"
    assert (a["dbt.invocation.nodes_total"], a["dbt.invocation.nodes_success"], a["dbt.invocation.nodes_failed"],
            a["dbt.invocation.nodes_warned"], a["dbt.invocation.nodes_skipped"]) == (5, 2, 1, 1, 1)


def test_a_warning_without_failure_makes_the_run_status_warn():
    end = run_macro("it_end_run_render", {"results": [result("model.p.a", "success"),
                                                      result("test.p.t", "warn", "test")]})["records"][-1]
    assert end["level"] == "warn" and end["attrs"]["dbt.invocation.status"] == "warn"


def test_an_empty_run_still_ends():
    end = run_macro("it_end_run_render", {"results": []})["records"][-1]
    assert end["attrs"]["dbt.invocation.status"] == "success" and end["attrs"]["dbt.invocation.nodes_total"] == 0


def test_end_run_calls_emit_run_guarded():
    sql = run_macro("it_end_run_render", {"results": SUCCESS})["sql"]
    assert sql.startswith("EXECUTE IMMEDIATE $$")
    assert '."ICECORE_EMIT_RUN"(PARSE_JSON(\'' in sql
    assert "WHEN OTHER THEN RETURN 'icecore-error '" in sql


def test_emit_run_puts_base_attributes_on_every_record_and_event():
    items = [{"name": "icecore.node", "start_ns": 1, "end_ns": 2, "attrs": {"x.k": 1},
              "events": [{"name": "icecore.query", "ts_ns": 1, "attrs": {"snow.query.id": "q"}}]},
             {"type": "log", "level": "WARN", "message": "m", "attrs": {"y.k": True}}]
    span, log = run_macro("it_emit_run_render", {"items": items})["records"]
    for attrs in (span["attrs"], span["events"][0]["attrs"], log["attrs"]):
        assert attrs["dbt.invocation_id"] and attrs["icecore.schema_version"] == 1
    assert span["attrs"]["x.k"] == 1 and span["events"][0]["attrs"]["snow.query.id"] == "q"
    assert (span["start_ns"], span["end_ns"]) == (1, 2)
    assert log["level"] == "warn" and log["message"] == "m" and log["attrs"]["y.k"] is True


def test_emit_run_caps_a_log_message_and_flags_it():
    big = "m" * (100 * KB)
    log, = run_macro("it_emit_run_render", {"items": [{"type": "log", "message": big, "attrs": {}}]})["records"]
    assert len(json.dumps(log["message"])) + len(json.dumps(log["attrs"])) <= 64 * KB + 64
    assert big.startswith(log["message"]) and len(log["message"]) > 60 * KB
    assert log["attrs"]["icecore.truncated"] is True and log["attrs"]["icecore.truncated_keys"] == "icecore.message"


def test_emit_run_chunks_by_bytes_only():
    small = [{"type": "log", "message": str(i), "attrs": {"i": i}} for i in range(250)]
    assert run_macro("it_emit_run_render", {"items": small})["chunk_sizes"] == [250]
    big = [{"type": "log", "message": "x", "attrs": {"i": i, "v": "z" * (60 * KB)}} for i in range(20)]
    out = run_macro("it_emit_run_render", {"items": big})
    assert sum(out["chunk_sizes"]) == 20 and len(out["chunk_sizes"]) >= 3
    assert all(b <= 512 * KB for b in out["chunk_bytes"])


def test_integration_project_wires_both_hooks_next_to_the_s3_probe():
    cfg = yaml.safe_load(open(IT_DIR / "dbt_project.yml"))
    assert cfg["on-run-start"] == ["{{ s3_hook_probe('start') if var('s3_probe', false) else '' }}",
                                   "{{ icecore.start_run() }}"]
    assert cfg["on-run-end"] == ["{{ s3_hook_probe('end') if var('s3_probe', false) else '' }}",
                                 "{{ icecore.end_run(results) }}"]


def test_readme_install_section_shows_the_two_hook_lines():
    readme = (REPO / "README.md").read_text()
    install = readme[readme.index("## Try it in 60 seconds"):readme.index("## How it works")]
    assert 'on-run-start: "{{ icecore.start_run() }}"' in install
    assert 'on-run-end: "{{ icecore.end_run(results) }}"' in install
    assert "telemetry: false" in install


def test_macro_docs_have_no_jinja():
    # dbt renders yml descriptions: a hook line written there would run.
    offenders = [str(p.relative_to(REPO)) for p in (REPO / "macros").rglob("*.yml")
                 if "{{" in p.read_text() or "{%" in p.read_text()]
    assert not offenders, offenders


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture
def views(sf, run_id):
    yield run_id
    drop_fixture_views(sf, run_id)


@pytest.mark.sandbox
def test_build_writes_one_start_and_one_end_record(sf, obs, views):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_a+"], views, obs)
    assert res.success, res.exception
    invocation_id = run_results["metadata"]["invocation_id"]
    rows = invocation_records(sf, obs, invocation_id, min_rows=2, since=since)
    starts, ends = run_start_events(rows), run_end_logs(rows)
    assert len(starts) == 1 and len(ends) == 1, [(r["record_type"], r["record"]) for r in rows]
    start, end = starts[0], ends[0]
    assert start["record_attributes"]["dbt.invocation_id"] == end["record_attributes"]["dbt.invocation_id"] == invocation_id
    assert start["record_attributes"]["dbt.command"] == "build"
    assert start["scope"]["name"] == "global" and end["scope"]["name"] == "icecore"
    a = end["record_attributes"]
    assert a["dbt.invocation.status"] == "warn"  # the accepted_values test on hooks_c warns
    assert (a["dbt.invocation.nodes_total"], a["dbt.invocation.nodes_success"], a["dbt.invocation.nodes_failed"],
            a["dbt.invocation.nodes_warned"]) == (len(node_results(run_results)), 3, 0, 1)


@pytest.mark.sandbox
def test_telemetry_false_writes_nothing(sf, obs, views):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_a"], views, obs, telemetry=False)
    assert res.success, res.exception
    assert_no_invocation_records(sf, obs, run_results["metadata"]["invocation_id"], since=since)


@pytest.mark.sandbox
def test_failing_model_ends_with_status_error_and_one_failed_node(sf, obs, views):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_a", "hooks_fail"], views, obs,
                                      extra_vars={"hooks_fail": True})
    assert not res.success
    assert sorted(r["status"] for r in node_results(run_results)) == ["error", "success"]
    rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"], min_rows=2, since=since)
    end, = run_end_logs(rows)
    assert end["record"]["severity_text"] == "ERROR"
    assert end["record_attributes"]["dbt.invocation.status"] == "error"
    assert end["record_attributes"]["dbt.invocation.nodes_failed"] == 1
    assert len(run_start_events(rows)) == 1
