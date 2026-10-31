"""UC2 schema signatures and diffs: icecore.get_columns_signature (SHOW COLUMNS) and icecore.diff_schema.

The macros are called through the wrappers in integration_tests/macros/test_support/it_schema.sql, which log
their results as `icecore_it_result:<json>`. diff_schema is pure Jinja, so its tests run offline.
"""
import json
import os

import pytest
from dbt.cli.main import dbtRunner

from conftest import IT_DIR
from test_offline import PLACEHOLDER_ENV

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES")
RESULT_PREFIX = "icecore_it_result:"
NUMBER = "NUMBER(38,0)"
VARCHAR = "VARCHAR(16777216)"
EMPTY = {"added": [], "removed": [], "type_changed": []}


def run_macro(macro: str, args: dict):
    """Run a macro through dbt run-operation and return the JSON value it logged with RESULT_PREFIX."""
    seen = []

    def capture(event):
        msg = getattr(event.data, "msg", "")
        if event.info.name == "JinjaLogInfo" and msg.startswith(RESULT_PREFIX):
            seen.append(json.loads(msg[len(RESULT_PREFIX):]))

    res = dbtRunner(callbacks=[capture]).invoke(
        ["run-operation", macro, "--args", json.dumps(args), "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]
    )
    detail = res.exception or [getattr(r, "message", r) for r in getattr(res.result, "results", [])]
    assert res.success, f"{macro} failed: {detail}"
    return seen[-1] if seen else None


def quoted(*parts: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def col(name: str, data_type: str) -> dict:
    return {"name": name, "data_type": data_type}


# --- offline: diff_schema is pure Jinja ------------------------------------------------------------

@pytest.fixture(scope="module")
def offline_dbt():
    saved = {k: os.environ.get(k) for k in PLACEHOLDER_ENV}
    for key, value in PLACEHOLDER_ENV.items():
        os.environ.setdefault(key, value)
    assert dbtRunner().invoke(["deps", "--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]).success
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)


def diff(previous, current):
    return run_macro("it_diff_schema", {"previous": previous, "current": current})


def test_diff_lists_an_added_column(offline_dbt):
    before = [col("ID", NUMBER)]
    after = [col("ID", NUMBER), col("EXTRA", "BOOLEAN")]
    assert diff(before, after) == {"added": [col("EXTRA", "BOOLEAN")], "removed": [], "type_changed": []}


def test_diff_lists_a_removed_column(offline_dbt):
    before = [col("ID", NUMBER), col("OLD", "DATE")]
    after = [col("ID", NUMBER)]
    assert diff(before, after) == {"added": [], "removed": [col("OLD", "DATE")], "type_changed": []}


def test_diff_lists_from_and_to_types_on_a_type_change(offline_dbt):
    before = [col("ID", NUMBER)]
    after = [col("ID", VARCHAR)]
    assert diff(before, after) == {
        "added": [], "removed": [], "type_changed": [{"name": "ID", "from_type": NUMBER, "to_type": VARCHAR}],
    }


def test_diff_is_empty_without_change(offline_dbt):
    sig = [col("ID", NUMBER), col("NOTE", VARCHAR)]
    assert diff(sig, list(reversed(sig))) == EMPTY


def test_diff_without_previous_signature_is_a_baseline(offline_dbt):
    assert diff(None, [col("ID", NUMBER)]) == EMPTY


def test_diff_keeps_column_names_case_sensitive(offline_dbt):
    # "id" (quoted, lower case) and ID are two different Snowflake columns
    assert diff([col("ID", NUMBER)], [col("id", NUMBER)]) == {
        "added": [col("id", NUMBER)], "removed": [col("ID", NUMBER)], "type_changed": [],
    }


# --- sandbox: signatures from SHOW COLUMNS ---------------------------------------------------------

def rel(identifier: str, quoted_identifier: bool = False) -> dict:
    return {"database": DATABASE, "schema": SCHEMA, "identifier": identifier, "quoted": quoted_identifier}


@pytest.mark.sandbox
def test_signature_and_diff_after_a_rebuild(dbt, sf, run_id):
    name = f"UC2_SCHEMA_{run_id}".upper()
    target = quoted(DATABASE, SCHEMA, name)
    cur = sf.cursor()
    cur.execute(f"create table {target} (id number, note varchar, old date)")
    try:
        first = run_macro("it_schema", {**rel(name.lower())})
        assert first["signature"] == [col("ID", NUMBER), col("NOTE", VARCHAR), col("OLD", "DATE")]
        again = run_macro("it_schema", {**rel(name.lower()), "previous": first["signature"]})
        assert again["diff"] == EMPTY
        cur.execute(f"create or replace table {target} (id varchar, note varchar, extra boolean)")
        after = run_macro("it_schema", {**rel(name.lower()), "previous": first["signature"]})
        assert after["diff"] == {
            "added": [col("EXTRA", "BOOLEAN")],
            "removed": [col("OLD", "DATE")],
            "type_changed": [{"name": "ID", "from_type": NUMBER, "to_type": VARCHAR}],
        }
    finally:
        cur.execute(f"drop table if exists {target}")


@pytest.mark.sandbox
def test_signature_types_are_normalised(dbt, sf, run_id):
    name = f"UC2_TYPES_{run_id}".upper()
    target = quoted(DATABASE, SCHEMA, name)
    sf.cursor().execute(
        f"create table {target} (n number(10,2), s varchar(20), t timestamp_ntz, f float, b boolean, v variant, d date, bin binary(8))"
    )
    try:
        sig = run_macro("it_schema", rel(name.lower()))["signature"]
        assert sig == [
            col("N", "NUMBER(10,2)"), col("S", "VARCHAR(20)"), col("T", "TIMESTAMP_NTZ(9)"), col("F", "FLOAT"),
            col("B", "BOOLEAN"), col("V", "VARIANT"), col("D", "DATE"), col("BIN", "BINARY(8)"),
        ]
    finally:
        sf.cursor().execute(f"drop table if exists {target}")


@pytest.mark.sandbox
def test_signature_of_a_quoted_mixed_case_table_and_a_view(dbt, sf, run_id):
    name = f'Mixed Case "Orders" {run_id}'
    view = f"UC2_SCHEMA_V_{run_id}".upper()
    target, view_target = quoted(DATABASE, SCHEMA, name), quoted(DATABASE, SCHEMA, view)
    cur = sf.cursor()
    cur.execute(f'create table {target} ("order Id" number, note varchar)')
    cur.execute(f'create view {view_target} as select "order Id", note from {target}')
    try:
        assert run_macro("it_schema", rel(name, True))["signature"] == [col("order Id", NUMBER), col("NOTE", VARCHAR)]
        assert run_macro("it_schema", rel(view.lower()))["signature"] == [col("order Id", NUMBER), col("NOTE", VARCHAR)]
    finally:
        cur.execute(f"drop view if exists {view_target}")
        cur.execute(f"drop table if exists {target}")


@pytest.mark.sandbox
def test_signature_of_a_missing_table_is_none_without_failing(dbt, run_id):
    assert run_macro("it_schema", rel(f"UC2_MISSING_{run_id}"))["signature"] is None
