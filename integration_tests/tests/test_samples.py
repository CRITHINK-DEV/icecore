"""UC1 failed-row samples: emit_samples (macros/telemetry/emit_sample.sql), an end-of-run emitter of end_run behind
var icecore.samples (default false: opt-in, samples copy row values).

For a failed test whose failures dbt stored (store_failures), one LOG at INFO with `icecore.kind = sample`: up to
var sample_rows (default 10) rows of the failures relation, read through a guarded query, as a JSON list in
`dbt.sample.rows`. Rows beyond payload_cap_kb are dropped from the end and `dbt.sample.truncated` is true. A test
that only warned gets no sample unless var samples_on_warn is true.

Offline tests render through integration_tests/macros/test_support/it_samples.sql (the query itself needs
Snowflake). Sandbox tests run a real `dbt build` of models/hooks against an obs schema of their own.
"""
import json

import pytest
import yaml

from helpers.macros import IT_DIR, run_macro
from helpers.run_hooks import (
    DATABASE, SCHEMA, assert_no_invocation_records, drop_fixture_views, drop_stored_failures, invocation_records,
    obs_schema, run_with_hooks, snowflake_since,
)

REPO = IT_DIR.parent
KB = 1024
RELATION = '"DB"."S_DBT_TEST__AUDIT"."unique_orders_id"'


def with_vars(icecore_vars: dict) -> list[str]:
    return ["--vars", json.dumps({"icecore": icecore_vars})]


def test_result(name="unique_orders_id", status="fail", store_failures=True, failures=3, relation=RELATION,
                severity="ERROR"):
    node = {"unique_id": f"test.p.{name}", "name": name, "resource_type": "test", "column_name": "id",
            "test_metadata": {"name": "unique"}, "relation_name": relation,
            "config": {"materialized": "test", "severity": severity, "store_failures": store_failures}}
    return {"unique_id": node["unique_id"], "status": status, "timing": [], "execution_time": 0.5,
            "adapter_response": {"query_id": "01q-t"}, "message": f"Got {failures} results", "failures": failures,
            "node": node}


def sample_logs(records):
    return [r for r in records if r.get("type") == "log" and r["attrs"].get("icecore.kind") == "sample"]


def candidates(results, **settings):
    return run_macro("it_sample_candidates", {"results": results}, with_vars(settings) if settings else None)


# --- offline --------------------------------------------------------------------------------------

def test_emitter_is_listed_behind_var_samples_default_false():
    emitters = {e["name"]: e for e in run_macro("it_end_run_emitters", {})}
    assert emitters["samples"]["var"] == "samples" and emitters["samples"]["default"] is False


def test_default_vars_give_no_sample_and_run_no_query():
    # Offline any query would fail to connect: success proves end_run ran none for a failed test.
    records = run_macro("it_end_run_render", {"results": [test_result()]})["records"]
    assert not sample_logs(records)


def test_excluded_columns_are_masked_in_the_query_not_in_dbt():
    for setting in (["unique_field", "EMAIL"], "unique_field, email"):
        sql = run_macro("it_sample_sql", {"relation_name": RELATION, "limit": 5, "with_excluded": True},
                        with_vars({"sample_exclude_columns": setting}))
        assert "'UNIQUE_FIELD'" in sql and "'EMAIL'" in sql and "to_variant('?')" in sql and "upper(f.key)" in sql
    plain = run_macro("it_sample_sql", {"relation_name": RELATION, "limit": 5, "with_excluded": True})
    assert "to_variant" not in plain  # nothing excluded: the plain query


def test_excluded_names_are_validated_and_fail_closed():
    names = ["email", "Col_1", "A", "a b", "o'x", "x'); drop table t; --", "a\n", "$$", "\u00e9", "a-b"]
    got = run_macro("it_sample_validate", {"names": names, "relations": []})["names"]
    assert got == [True, True, True] + [False] * 7
    # one bad name and no sample is read: end_run with a failing test sends no query (offline: it would not connect)
    records = run_macro("it_end_run_render", {"results": [test_result()]},
                        with_vars({"samples": True, "sample_exclude_columns": ["ok", "bad'name"]}))["records"]
    assert not sample_logs(records)


def test_relation_names_are_validated_as_identifiers():
    ok = ['"DB"."S_DBT_TEST__AUDIT"."unique_orders_id"', 'DB.SCH.T1', '"a""b"."c d".e', "T$1"]
    bad = ['DB.SCH.T; drop table x', "DB.SCH.T\n", '"unclosed', 'a.b.c.d', "x' or '1'='1", '"a"."b"; select 1', ""]
    got = run_macro("it_sample_validate", {"names": [], "relations": ok + bad})["relations"]
    assert got == [True] * len(ok) + [False] * len(bad)
    result = test_result(relation='"DB"."S"."t"; drop table x')
    records = run_macro("it_end_run_render", {"results": [result]}, with_vars({"samples": True}))["records"]
    assert not sample_logs(records)


def test_a_failed_test_with_stored_failures_is_a_candidate():
    assert candidates([test_result()])["ids"] == ["test.p.unique_orders_id"]


def test_warn_severity_is_off_by_default_and_on_with_samples_on_warn():
    warned = [test_result("warned", status="warn", severity="WARN")]
    assert candidates(warned)["ids"] == []
    assert candidates(warned, samples_on_warn=True)["ids"] == ["test.p.warned"]
    assert candidates(warned, samples_on_warn="true")["ids"] == ["test.p.warned"]




@pytest.mark.parametrize("result", [
    test_result("passed", status="pass"),
    test_result("errored", status="error"),
    test_result("nostore", store_failures=False, relation=None),
    test_result("norelation", relation=None),
    test_result("zero", failures=0),
])
def test_other_results_are_not_candidates(result):
    assert candidates([result])["ids"] == []


def test_a_model_is_never_a_candidate():
    model = {"unique_id": "model.p.m", "status": "error", "failures": None,
             "node": {"unique_id": "model.p.m", "resource_type": "model", "name": "m", "config": {}}}
    assert candidates([model])["ids"] == []


def test_sample_rows_default_and_setting():
    assert candidates([])["limit"] == 10
    assert candidates([], sample_rows=3)["limit"] == 3
    assert candidates([], sample_rows="4")["limit"] == 4
    assert candidates([], sample_rows=0)["limit"] == 1 and candidates([], sample_rows="junk")["limit"] == 10


def test_query_selects_up_to_n_rows_of_the_failures_relation():
    sql = run_macro("it_sample_sql", {"relation_name": RELATION, "limit": 5})
    assert RELATION in sql and "limit 5" in sql.lower() and "object_construct_keep_null" in sql.lower()


def test_sample_record_is_info_with_rows_and_test_attributes():
    rows = [{"unique_field": 7, "n_records": 2}, {"unique_field": 9, "n_records": 3}]
    record = run_macro("it_sample_render", {"result": test_result(), "rows": rows})
    a = record["attrs"]
    assert record["type"] == "log" and record["level"] == "info"
    assert a["icecore.kind"] == "sample" and a["dbt.invocation_id"]
    assert a["dbt.node.unique_id"] == "test.p.unique_orders_id" and a["dbt.test.name"] == "unique"
    assert a["dbt.test.failures"] == 3 and a["dbt.result.status"] == "fail"
    assert json.loads(a["dbt.sample.rows"]) == rows
    assert a["dbt.sample.row_count"] == 2 and a["dbt.sample.truncated"] is False
    assert "icecore.truncated" not in a


def test_sample_larger_than_the_cap_is_cut_row_wise_and_flagged():
    rows = [{"unique_field": "x" * 20000 + str(i), "n_records": 2} for i in range(10)]
    record = run_macro("it_sample_render", {"result": test_result(), "rows": rows})
    a = record["attrs"]
    kept = json.loads(a["dbt.sample.rows"])  # still valid JSON: cut by rows, never mid-row
    assert a["dbt.sample.truncated"] is True
    assert 1 <= len(kept) < 10 and kept == rows[:len(kept)] and a["dbt.sample.row_count"] == len(kept)
    assert len(json.dumps(record["attrs"])) + len(record["message"]) <= 64 * KB
    assert "icecore.truncated" not in a  # the generic string cut never had to run


def test_sample_follows_payload_cap_kb():
    rows = [{"unique_field": "x" * 1000 + str(i)} for i in range(10)]
    full = run_macro("it_sample_render", {"result": test_result(), "rows": rows})["attrs"]
    small = run_macro("it_sample_render", {"result": test_result(), "rows": rows},
                      with_vars({"payload_cap_kb": 4}))["attrs"]
    assert full["dbt.sample.truncated"] is False and small["dbt.sample.truncated"] is True
    assert small["dbt.sample.row_count"] < 10


def test_no_row_fits_gives_an_empty_flagged_sample():
    rows = [{"unique_field": "x" * 200 * KB}]
    a = run_macro("it_sample_render", {"result": test_result(), "rows": rows})["attrs"]
    assert json.loads(a["dbt.sample.rows"]) == [] and a["dbt.sample.truncated"] is True


def test_end_run_without_candidates_adds_no_sample_and_runs_no_query():
    # Offline any query would fail to connect: success proves none ran for a warn under default vars.
    results = [test_result("warned", status="warn", severity="WARN"), test_result("passed", status="pass")]
    assert not sample_logs(run_macro("it_end_run_render", {"results": results})["records"])


def test_project_vars_docs_and_readme():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))["vars"]["icecore"]
    assert (cfg["samples"], cfg["sample_rows"], cfg["samples_on_warn"], cfg["sample_exclude_columns"]) == (False, 10, False, [])
    docs = yaml.safe_load(open(REPO / "macros/telemetry/emit_sample.yml"))
    assert [m["name"] for m in docs["macros"]] == ["emit_samples"]
    assert "{{" not in (REPO / "macros/telemetry/emit_sample.yml").read_text()
    readme = (REPO / "README.md").read_text()
    configuration = readme[readme.index("`result_message` controls"):]
    assert "sample" in configuration.split("## How it works")[0].lower()


# --- sandbox --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def obs(sf):
    with obs_schema(sf) as schema:
        yield schema


@pytest.fixture
def views(sf, run_id):
    yield run_id
    drop_fixture_views(sf, run_id)
    drop_stored_failures(sf, run_id)


def sample_rows_of(rows):
    return [r for r in rows if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "sample"]


DUP_TESTS = ["hooks_dup_unique_id", "hooks_dup_unique_code", "hooks_dup_code_not_null", "hooks_dup_unique_big"]


def build_tests(views, obs, tests, **settings):
    """Build hooks_dup with only `tests` (the others excluded: selecting a model brings its tests)."""
    others = [t for t in DUP_TESTS if t not in tests]
    settings = {"samples": True, **settings}  # opt-in; samples=None leaves the var out (the default)
    settings = {k: v for k, v in settings.items() if v is not None}
    res, run_results = run_with_hooks(["build", "--select", "hooks_dup", "--exclude", *others], views, obs, **settings)
    return res, run_results["metadata"]["invocation_id"], {r["unique_id"].split(".")[2]: r["status"]
                                                           for r in run_results["results"]}


@pytest.mark.sandbox
def test_failing_unique_test_lands_a_sample_with_the_duplicate_keys(sf, obs, views):
    since = snowflake_since(sf)
    res, invocation_id, statuses = build_tests(views, obs, ["hooks_dup_unique_id"])
    assert statuses["hooks_dup_unique_id"] == "fail"
    rows = invocation_records(sf, obs, invocation_id, min_rows=3, since=since)
    sample, = sample_rows_of(rows)
    assert sample["record"]["severity_text"] == "INFO" and sample["scope"]["name"] == "icecore"
    a = sample["record_attributes"]
    assert ".hooks_dup_unique_id." in a["dbt.node.unique_id"] and a["dbt.test.failures"] == 10
    kept = json.loads(a["dbt.sample.rows"])
    assert len(kept) == 10 and a["dbt.sample.truncated"] is False
    assert sorted(int(r["UNIQUE_FIELD"]) for r in kept) == list(range(1, 11))
    assert all(int(r["N_RECORDS"]) == 2 for r in kept)


@pytest.mark.sandbox
def test_sample_rows_limits_the_rows(sf, obs, views):
    since = snowflake_since(sf)
    res, invocation_id, _ = build_tests(views, obs, ["hooks_dup_unique_id"], sample_rows=3)
    sample, = sample_rows_of(invocation_records(sf, obs, invocation_id, min_rows=3, since=since))
    assert sample["record_attributes"]["dbt.sample.row_count"] == 3


@pytest.mark.sandbox
def test_wide_rows_are_truncated_and_flagged(sf, obs, views):
    since = snowflake_since(sf)
    res, invocation_id, statuses = build_tests(views, obs, ["hooks_dup_unique_big"])
    assert statuses["hooks_dup_unique_big"] == "fail"
    sample, = sample_rows_of(invocation_records(sf, obs, invocation_id, min_rows=3, since=since))
    a = sample["record_attributes"]
    assert a["dbt.sample.truncated"] is True
    kept = json.loads(a["dbt.sample.rows"])
    assert 1 <= len(kept) < 10 and a["dbt.sample.row_count"] == len(kept)


@pytest.mark.sandbox
def test_warn_severity_lands_no_sample_by_default_and_one_with_samples_on_warn(sf, obs, views):
    since = snowflake_since(sf)
    res, invocation_id, statuses = build_tests(views, obs, ["hooks_dup_unique_code"])
    assert statuses["hooks_dup_unique_code"] == "warn"
    rows = invocation_records(sf, obs, invocation_id, min_rows=3, since=since)
    assert not sample_rows_of(rows)

    since = snowflake_since(sf)
    res, invocation_id, _ = build_tests(views, obs, ["hooks_dup_unique_code"], samples_on_warn=True)
    sample, = sample_rows_of(invocation_records(sf, obs, invocation_id, min_rows=3, since=since))
    assert sample["record_attributes"]["dbt.result.status"] == "warn"


@pytest.mark.sandbox
def test_default_vars_and_telemetry_false_land_no_sample(sf, obs, views):
    since = snowflake_since(sf)
    res, invocation_id, _ = build_tests(views, obs, ["hooks_dup_unique_id"], samples=None)
    assert not sample_rows_of(invocation_records(sf, obs, invocation_id, min_rows=3, since=since))

    since = snowflake_since(sf)
    res, invocation_id, _ = build_tests(views, obs, ["hooks_dup_unique_id"], telemetry=False)
    assert_no_invocation_records(sf, obs, invocation_id, since=since)


@pytest.mark.sandbox
def test_excluded_column_is_question_marks_in_the_landed_sample(sf, obs, views):
    since = snowflake_since(sf)
    res, invocation_id, _ = build_tests(views, obs, ["hooks_dup_unique_id"], sample_exclude_columns=["unique_field"])
    sample, = sample_rows_of(invocation_records(sf, obs, invocation_id, min_rows=3, since=since))
    kept = json.loads(sample["record_attributes"]["dbt.sample.rows"])
    assert len(kept) == 10 and {r["UNIQUE_FIELD"] for r in kept} == {"?"} and all(int(r["N_RECORDS"]) == 2 for r in kept)


@pytest.mark.sandbox
def test_excluded_values_never_leave_snowflake(sf, run_id):
    """Run the emitter's own query on a real table: the raw value is not in what comes back."""
    table = f'"{DATABASE}"."{SCHEMA}"."HOOKS_MASK_{run_id.upper()}"'
    cur = sf.cursor()
    cur.execute(f'create table {table} as select * from (values (1, \'jane@x.com\', \'keep\'), '
                f'(2, null, \'keep2\')) t("ID", "Email", "note")')
    try:
        sql = run_macro("it_sample_sql", {"relation_name": table, "limit": 10, "with_excluded": True},
                        with_vars({"sample_exclude_columns": ["EMAIL"]}))  # the column is mixed case: "Email"
        rows = json.loads(cur.execute(sql).fetchone()[0])
        assert "jane@x.com" not in json.dumps(rows)
        assert sorted((r["ID"], r["Email"], r["note"]) for r in rows) == [(1, "?", "keep"), (2, "?", "keep2")]
        # no exclusion: the plain query returns the value
        plain = run_macro("it_sample_sql", {"relation_name": table, "limit": 10})
        assert "jane@x.com" in cur.execute(plain).fetchone()[0]
    finally:
        cur.execute(f"drop table if exists {table}")
