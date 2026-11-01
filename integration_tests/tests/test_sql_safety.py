"""Security: icecore._sql (macros/util/sql.sql) is the one helper that builds SQL literals and identifiers, and no value
can split a statement (a `$$` closing a body, a quote, a backslash, a semicolon, a newline).

split_statements is the connector's splitter, the one dbt uses to run a multi-statement string: a payload that makes
it return more than one statement would run as separate SQL.
"""
import io
import json
import re
from pathlib import Path

import pytest
from snowflake.connector.util_text import split_statements

from helpers.macros import MacroFailed, call_macro, run_macro

REPO = Path(__file__).resolve().parents[2]

PAYLOADS = [
    "X$$; GRANT ROLE ICECORE_SETUP TO ROLE DBT_ROLE; --",
    "$$", "'", "\\", ";", "\n", "\r\n", "a'; drop table x; --", "\\'$$; select 1; $$", "it's \\ a $ $$ ; \n end",
    "emoji 😀 é 漢",
]


def payloads_in(monkeypatch, values: list[str]) -> None:
    monkeypatch.setenv("IT_PAYLOADS", json.dumps(values))


def split(sql: str) -> list[str]:
    return [s for s, _ in split_statements(io.StringIO(sql)) if s.strip()]


# --- _sql -----------------------------------------------------------------------------------------

def test_string_context_never_leaves_a_dollar_or_a_raw_quote(monkeypatch):
    payloads_in(monkeypatch, PAYLOADS)
    out = run_macro("it_sql", {"context": "string"})
    for payload, row in zip(PAYLOADS, out):
        literal = row["sql"]
        assert literal.startswith("'") and literal.endswith("'")
        assert "$" not in literal and "\n" not in literal and "\r" not in literal
        assert len(split(f"select {literal}")) == 1, payload
        assert len(split(f"CREATE TASK t AS EXECUTE IMMEDIATE $$ BEGIN SELECT {literal}; END; $$")) == 1, payload


def test_string_context_round_trips_the_value(monkeypatch):
    # what Snowflake reads back: \\ \' \x24 \n \r undone in one pass
    payloads_in(monkeypatch, PAYLOADS)
    out = run_macro("it_sql", {"context": "string"})
    for payload, row in zip(PAYLOADS, out):
        inner = row["sql"][1:-1]
        back = re.sub(r"\\(x24|n|r|\\|')", lambda m: {"x24": "$", "n": "\n", "r": "\r"}.get(m.group(1), m.group(1)), inner)
        assert back == payload


IDENT_BAD = ["X$$; GRANT ROLE A TO ROLE B; --", "A$$B", 'A"B', "A\\B", "A\nB", "A B", "A;B", "A\n", "A\r", "", "A\x00B", "É"]


def test_ident_context_accepts_only_letters_digits_and_underscore(monkeypatch):
    ok = ["ICECORE_OBS", "a1_", "9", "icecore_obs_ab12"]
    payloads_in(monkeypatch, ok + IDENT_BAD)
    out = run_macro("it_sql", {"context": "ident"})
    for value, row in zip(ok, out):
        assert row["error"] is None and row["sql"] == f'"{value}"'
    for value, row in zip(IDENT_BAD, out[len(ok):]):
        assert row["error"] and "SQL identifier" in row["error"], repr(value)


def test_ident_context_anchors_with_backslash_z_so_a_trailing_newline_is_refused(monkeypatch):
    payloads_in(monkeypatch, ["ICECORE_OBS\n"])
    out = run_macro("it_sql", {"context": "ident"})
    assert out[0]["error"]


def test_ident_context_takes_a_list_of_parts():
    assert call_macro("_sql", value=["DB", "SCH"], context="ident") == '"DB"."SCH"'
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        call_macro("_sql", value=["DB", "SCH$$"], context="ident")


def test_name_context_keeps_quotes_and_spaces_and_refuses_dollar_and_control_characters(monkeypatch):
    payloads_in(monkeypatch, ['Mixed Case "Orders"', "a-b", "x$$y", "x\ny", "x\x7fy"])
    out = run_macro("it_sql", {"context": "name"})
    assert out[0]["sql"] == '"Mixed Case ""Orders"""' and out[1]["sql"] == '"a-b"'
    assert all(r["error"] and "SQL name" in r["error"] for r in out[2:])


def test_an_unknown_context_raises():
    with pytest.raises(MacroFailed, match="context must be"):
        call_macro("_sql", value="x", context="bogus")


# --- every renderer, every payload -------------------------------------------------------------------

def test_every_string_renderer_yields_exactly_one_statement_for_every_payload(monkeypatch):
    payloads_in(monkeypatch, PAYLOADS)
    out = run_macro("it_string_renderers", {})
    assert set(out) >= {"literal", "guarded_call", "guarded_ddl", "try_show", "try_scalar", "columns_block",
                        "timestamp", "changes_source", "body"}
    for renderer, statements in out.items():
        assert len(statements) == len(PAYLOADS)
        for payload, sql in zip(PAYLOADS, statements):
            assert len(split(sql)) == 1, (renderer, payload, sql[:200])


# --- hostile identifiers fail at compile time ------------------------------------------------------------

HOSTILE = "X$$; GRANT ROLE ICECORE_SETUP TO ROLE DBT_ROLE; --"


def with_obs_schema(value: str) -> list[str]:
    return ["--vars", json.dumps({"icecore": {"obs_schema": value}})]


@pytest.mark.parametrize("name,kwargs", [
    ("setup", {}), ("doctor", {}), ("_icecore_procedure", {"name": "ICECORE_EMIT"}), ("obs_schema", {}),
    ("create_emit_procedures", {}), ("_icecore_emit_procedures_ddl", {}),
])
def test_a_hostile_obs_schema_var_fails_at_compile_time_with_a_clear_error(name, kwargs):
    # Offline, any query would fail with a connection error instead: this message proves nothing was sent.
    with pytest.raises(MacroFailed) as raised:
        run_macro("it_call", {"name": name, "kwargs": kwargs}, with_obs_schema(HOSTILE))
    assert "invalid SQL identifier" in str(raised.value)


@pytest.mark.parametrize("name", ["setup", "doctor"])
def test_a_hostile_schema_argument_of_setup_and_doctor_fails_at_compile_time(name):
    with pytest.raises(MacroFailed) as raised:
        run_macro("it_call", {"name": name, "kwargs": {"schema": HOSTILE}})
    assert "invalid SQL identifier" in str(raised.value)


def test_a_hostile_view_name_or_database_is_refused():
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        run_macro("it_call", {"name": "_icecore_view_ddl", "kwargs": {"name": HOSTILE, "sql": "select 1"}})
    with pytest.raises(MacroFailed, match="invalid SQL name"):
        call_macro("base_events", event_table="T", database="D$$X")


def test_a_procedure_name_that_is_not_built_by_the_package_is_refused_by_the_guard():
    with pytest.raises(MacroFailed, match="invalid procedure name"):
        call_macro("_icecore_guarded_call_sql", procedure="P$$; GRANT x", items=[])


# --- the codebase ------------------------------------------------------------------------------------------

def test_only_icecore_sql_builds_literals_and_identifiers():
    # The old helpers are one-line wrappers that call icecore._sql, and no macro builds a quoted identifier or a
    # string literal by hand (no replace of a quote in the package outside macros/util/sql.sql).
    offenders = []
    for path in (REPO / "macros").rglob("*.sql"):
        if path.name == "sql.sql":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("{#-")[0]
            if re.search(r"""replace\(\s*(['"])(\\\\|'|")\1|replace\(["']'["']""", code) or re.search(r"""'"'\s*~""", code):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, offenders
