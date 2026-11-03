"""S08 regression scenarios: the bad days, each named after the review finding it guards.

Three kinds of test:

- End-to-end scenarios (sandbox): a real `dbt build` through icecore's hooks into an obs schema of its own.
  A failing model, an empty selection, an interrupted run, values that once broke the write path (a `$$`, a quote
  and a backslash, a Decimal, a nested object) landing in a sample, and a missing obs schema.
- Offline scenarios: redaction modes.
- A registry, FINDINGS, of the findings whose regression tests live next to their module (tests of the same
  macros). It maps each finding to those tests, and `test_every_finding_has_its_live_test` fails when one is
  renamed or deleted, so a finding cannot silently lose its guard.

Not covered: unattributable cost queries. Cost attribution (attach_cost, UC4) is not in the package yet, so there
is no behaviour to guard; the scenario belongs with that story.
"""
import ast
import json
import uuid
from pathlib import Path

import pytest

from helpers.macros import IT_DIR, run_macro
from helpers.run_hooks import (
    assert_no_invocation_records, drop_fixture_views, drop_stored_failures, icecore_vars, invocation_records,
    node_results, obs_schema, run_end_logs, run_with_hooks, snowflake_since,
)
from helpers.events import wait_for_events

TESTS = Path(__file__).parent
DUP = ["hooks_dup_unique_id", "hooks_dup_unique_code", "hooks_dup_code_not_null", "hooks_dup_unique_big"]
WEIRD = ["hooks_weird_unique_k", "hooks_weird_unique_d", "hooks_weird_unique_v"]
ALL_TESTS = DUP + WEIRD

# finding -> (test file, test function). The tests are in the module's own file, on the real behaviour.
FINDINGS = {
    "emit_decimal_from_run_query_is_not_json_serializable":
        ("test_failure_isolation.py", "test_run_query_values_decimal_dates_and_nested_land_as_json"),
    "nested_values_cleaned_recursively_and_dates_iso":
        ("test_failure_isolation.py", "test_dates_are_iso_and_nested_values_are_cleaned_recursively"),
    "system_add_event_drops_objects_so_emit_sends_json_text":
        ("test_failure_isolation.py", "test_emit_writes_nested_values_as_json_text_and_emit_run_keeps_them_native"),
    "payload_cannot_close_the_dollar_block":
        ("test_emit.py", "test_call_is_guarded_and_escaped"),
    "chunk_size_measures_the_escaped_literal":
        ("test_failure_isolation.py", "test_chunks_measure_the_escaped_literal_not_the_raw_json"),
    "missing_procedure_never_fails_the_run":
        ("test_emit.py", "test_missing_procedure_logs_a_warning_and_never_fails"),
    "missing_procedures_give_one_warning_per_hook":
        ("test_failure_isolation.py", "test_build_with_no_emitter_procedures_succeeds_with_one_warning_per_hook"),
    "many_failing_chunks_log_one_warning":
        ("test_failure_isolation.py", "test_many_failing_chunks_log_one_warning_for_the_hook"),
    "emitter_error_does_not_stop_the_next_emitter":
        ("test_failure_isolation.py", "test_an_emitter_that_returns_an_error_does_not_stop_the_next"),
    "records_survive_a_rollback_through_both_procedures":
        ("test_failure_isolation.py", "test_records_survive_a_rollback_through_both_procedures"),
    "unreadable_metric_with_thresholds_passed_forever":
        ("test_freshness_volume.py", "test_unreadable_metric_passes_bare_and_hits_severity_with_thresholds"),
    "failed_telemetry_call_changes_the_test_verdict":
        ("test_freshness_volume.py", "test_a_failed_telemetry_call_does_not_change_the_verdict"),
    "metric_history_count_query_was_unguarded":
        ("test_metric_history.py", "test_a_failing_read_warns_and_reads_as_insufficient_history"),
    "history_window_edge_excludes_records_inside_the_delay":
        ("test_metric_history.py", "test_a_record_newer_than_the_history_delay_is_excluded"),
    "window_boundary_rows_at_since_are_counted":
        ("test_columns.py", "test_timestamp_window_counts_rows_at_since"),
    "table_recreated_after_since_falls_back":
        ("test_columns.py", "test_table_recreated_after_since_falls_back_to_the_timestamp_column"),
    "only_coverage_errors_trigger_the_window_fallback":
        ("test_columns.py", "test_only_window_coverage_errors_trigger_the_timestamp_fallback"),
    "failed_columns_read_reported_as_column_not_found":
        ("test_columns.py", "test_a_failed_columns_read_is_not_reported_as_column_not_found"),
    "failing_model_message_must_be_redacted":
        ("test_node_results.py", "test_failing_model_result_message_is_redacted"),
    "redacted_message_capped_at_1000_characters":
        ("test_node_results.py", "test_redacted_message_is_capped_at_1000_characters"),
    "sample_wide_rows_cut_row_wise":
        ("test_samples.py", "test_wide_rows_are_truncated_and_flagged"),
    "sample_excluded_values_never_leave_snowflake":
        ("test_samples.py", "test_excluded_values_never_leave_snowflake"),
    "interrupted_run_is_running_in_the_view":
        ("test_run_views.py", "test_a_run_with_a_start_and_no_end_is_running"),
}


def test_every_finding_has_its_live_test():
    missing = []
    for finding, (file, name) in FINDINGS.items():
        path = TESTS / file
        functions = {n.name for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.FunctionDef)}
        if name not in functions:
            missing.append((finding, file, name))
    assert not missing, f"findings without their test: {missing}"


# --- offline: redaction ---------------------------------------------------------------------------

SECRET = "Numeric value 'jane@x.com' is not recognized; column \"o'k\" and 'it''s \\' end"


def messages(*modes):
    out = {}
    for mode in modes:
        out[mode] = run_macro("it_result_message", {"messages": [SECRET]},
                              ["--vars", json.dumps({"icecore": {"result_message": mode}})])[0]
    return out


def test_redaction_default_hides_every_quoted_literal():
    default = run_macro("it_result_message", {"messages": [SECRET]})[0]
    assert "jane@x.com" not in default and "o'k" not in default
    assert default.startswith("Numeric value '?' is not recognized")


def test_redaction_modes_full_and_none():
    got = messages("full", "none")
    assert got["full"] == SECRET and got["none"] is None


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture
def run(sf):
    """A run_id whose fixture views and stored failures are dropped afterwards."""
    run_id = uuid.uuid4().hex
    yield run_id
    drop_fixture_views(sf, run_id)
    drop_stored_failures(sf, run_id)


def only(tests):
    """--exclude for every hooks test not in `tests` (selecting a model brings its tests)."""
    return ["--exclude", *[t for t in ALL_TESTS if t not in tests]]


def kinds(rows):
    return {(r["record_type"], r["record"].get("name"), r["record_attributes"].get("icecore.kind")) for r in rows}


@pytest.mark.sandbox
def test_failing_model_still_lands_every_record_kind(sf, obs, run):
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(
        ["build", "--select", "hooks_a", "hooks_fail", "hooks_dup", *only(["hooks_dup_unique_id"])], run, obs,
        samples=True, extra_vars={"hooks_fail": True})
    assert not res.success
    nodes = node_results(run_results)
    statuses = sorted(r["status"] for r in nodes)
    assert "error" in statuses and "fail" in statuses and "success" in statuses
    queries = sum(1 for r in nodes if r["adapter_response"].get("query_id"))
    rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"],
                              min_rows=5 + 2 * len(nodes) + queries, since=since)
    assert kinds(rows) == {("SPAN_EVENT", "icecore.run_start", None), ("SPAN", "icecore.invocation", None),
                           ("SPAN", "icecore.node", None), ("SPAN_EVENT", "icecore.query", None),
                           ("LOG", None, "node_result"), ("LOG", None, "lineage"), ("LOG", None, "sample"),
                           ("LOG", None, "run_end")}
    end, = run_end_logs(rows)
    assert end["record_attributes"]["dbt.invocation.status"] == "error"
    assert end["record_attributes"]["dbt.invocation.nodes_total"] == len(nodes)
    results = {r["record_attributes"]["dbt.node.unique_id"]: r["record_attributes"]["dbt.result.status"]
               for r in rows if r["record_attributes"].get("icecore.kind") == "node_result"}
    assert sorted(results.values()) == statuses


@pytest.mark.sandbox
def test_empty_selection_builds_with_hooks_and_writes_nothing(sf, obs, run):
    """dbt runs no hooks when nothing is selected ("Nothing to do"), so icecore writes nothing and the build
    succeeds. (An end_run that does run with no results still ends `success` with 0 nodes: test_hooks.py,
    test_an_empty_run_still_ends.)"""
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", f"tag:nothing_has_this_{run}"], run, obs)
    assert res.success, res.exception
    assert node_results(run_results) == []
    assert_no_invocation_records(sf, obs, run_results["metadata"]["invocation_id"], since=since, wait_s=30)


@pytest.mark.sandbox
def test_interrupted_run_is_incomplete_in_the_views_after_the_next_run_ends(sf, obs, run):
    since = snowflake_since(sf)
    interrupted = f"it-interrupted-{run}"
    # a run that wrote its start and then died: no span, no end record
    run_macro("it_emit_events", {"events": [{"kind": "run_start", "attributes": {
        "dbt.invocation_id": interrupted, "dbt.command": "build", "dbt.runtime": "core",
        "dbt.invocation.started_at": "2026-01-01T00:00:00.000000Z"}}]}, ["--vars", json.dumps(icecore_vars(obs))])
    res, run_results = run_with_hooks(["build", "--select", "hooks_a"], run, obs)  # the next run
    assert res.success, res.exception
    ok = run_results["metadata"]["invocation_id"]
    invocation_records(sf, obs, ok, min_rows=3 + 2 * len(node_results(run_results)), since=since)
    wait_for_events(sf, "record_attributes:\"dbt.invocation_id\"::string = %(i)s", {"i": interrupted}, since=since,
                    settle_s=5)
    run_macro("it_create_views", {"schema": obs}, ["--vars", json.dumps(icecore_vars(obs))])
    from helpers.run_hooks import DATABASE
    from snowflake.connector import DictCursor
    rows = {r["INVOCATION_ID"]: r for r in sf.cursor(DictCursor).execute(
        f'select * from "{DATABASE}"."{obs}"."INVOCATIONS" where invocation_id in (%(a)s, %(b)s)',
        {"a": interrupted, "b": ok}).fetchall()}
    assert rows[interrupted]["STATUS"] in ("running", "unfinished")
    assert rows[interrupted]["ENDED_AT"] is None and rows[interrupted]["NODES_TOTAL"] is None
    assert rows[ok]["STATUS"] == "success" and rows[ok]["ENDED_AT"] is not None


@pytest.mark.sandbox
def test_values_that_broke_the_write_path_land_intact_in_a_sample(sf, obs, run):
    """A quote, `$$` and a backslash, a Decimal and a nested object, through the whole path: the test's stored
    failures, the guarded sample query, the JSON text attribute, ICECORE_EMIT_RUN, and back out of the table."""
    since = snowflake_since(sf)
    res, run_results = run_with_hooks(["build", "--select", "hooks_weird", *only(WEIRD)], run, obs, samples=True)
    assert not res.success  # the three unique tests fail
    nodes = node_results(run_results)
    rows = invocation_records(sf, obs, run_results["metadata"]["invocation_id"],
                              min_rows=5 + 2 * len(nodes), since=since)
    samples = {r["record_attributes"]["dbt.test.column"]: json.loads(r["record_attributes"]["dbt.sample.rows"])
               for r in rows if r["record_attributes"].get("icecore.kind") == "sample"}
    assert sorted(samples) == ["d", "k", "v"]
    (k_row,), (d_row,), (v_row,) = samples["k"], samples["d"], samples["v"]
    assert k_row["UNIQUE_FIELD"] == 'it\'s a $$ \\ "q" end' and k_row["N_RECORDS"] == 2
    assert d_row["UNIQUE_FIELD"] == 1.5 and isinstance(d_row["UNIQUE_FIELD"], float)
    assert v_row["UNIQUE_FIELD"] == {"a": [1, {"b": "x $$ \\ y"}]}
    assert not [r for r in rows if r["record_attributes"].get("icecore.kind") == "run_end"][0][
        "record_attributes"].get("icecore.truncated")


@pytest.mark.sandbox
def test_missing_obs_schema_does_not_change_the_run(sf, run):
    """No schema, so no procedures: every CALL fails inside its guard; dbt's own results stay what they are."""
    missing = f"ICECORE_OBS_MISSING_{uuid.uuid4().hex}".upper()
    ok, ok_results = run_with_hooks(["build", "--select", "hooks_a"], run, missing)
    assert ok.success, ok.exception
    bad, bad_results = run_with_hooks(["build", "--select", "hooks_a", "hooks_fail"], run, missing,
                                      extra_vars={"hooks_fail": True})
    assert not bad.success
    assert sorted(r["status"] for r in node_results(bad_results)) == ["error", "success"]
    since = snowflake_since(sf)
    assert_no_invocation_records(sf, missing, ok_results["metadata"]["invocation_id"], since=since, wait_s=5)
