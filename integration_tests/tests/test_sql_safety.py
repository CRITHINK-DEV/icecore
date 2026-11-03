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

from helpers.macros import MacroFailed, call_macro, run_macro, run_macro_with_logs

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


def test_ident_context_accepts_only_letters_digits_underscore_and_a_single_dollar_sign(monkeypatch):
    ok = ["ICECORE_OBS", "a1_", "9", "icecore_obs_ab12", "MY$DB", "A$"]
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


def test_name_context_keeps_quotes_spaces_and_a_single_dollar_and_refuses_double_dollar_and_control_characters(monkeypatch):
    payloads_in(monkeypatch, ['Mixed Case "Orders"', "a-b", "MY$DB", "x$$y", "x\ny", "x\x7fy", "x\r"])
    out = run_macro("it_sql", {"context": "name"})
    assert out[0]["sql"] == '"Mixed Case ""Orders"""' and out[1]["sql"] == '"a-b"' and out[2]["sql"] == '"MY$DB"'
    assert all(r["error"] and "SQL name" in r["error"] for r in out[3:])


def test_an_account_database_named_my_dollar_db_works_and_x_dollar_dollar_y_a_newline_and_a_quote_are_refused():
    assert call_macro("_sql", value="MY$DB", context="ident") == '"MY$DB"'
    assert call_macro("_sql", value=["MY$DB", "S"], context="ident") == '"MY$DB"."S"'
    assert call_macro("_sql", value="MY$DB", context="name") == '"MY$DB"'
    for context in ("ident", "name"):
        for bad in ("X$$Y", "X\nY", "X\x00Y"):
            with pytest.raises(MacroFailed, match="invalid SQL"):
                call_macro("_sql", value=bad, context=context)
    with pytest.raises(MacroFailed, match="invalid SQL identifier"):
        call_macro("_sql", value='X"Y', context="ident")


def test_the_procedure_of_a_database_with_a_single_dollar_sign_renders_and_stays_one_statement(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "MY$DB")
    out = run_macro("it_procedure_for_target", {})
    assert out["error"] is None and out["procedure"] == '"MY$DB"."ICECORE_OBS"."ICECORE_EMIT"'
    assert len(split(out["sql"])) == 1


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


NAME_PAYLOADS = [
    "$$", "'", "\\", ";", "\n", '"', "a'b", 'a"b', "a;b", "\\'; drop table x; --", '"; drop table x; --', "a\\b",
    "MY-DB", "My DB", 'Mixed Case "Orders"',
    "\uff04\uff04", "\u02bc; drop", "\u201cx\u201d", "\uff07; select 1", "\u2018a\u2019", "\u0430dmin", "emoji 😀 漢",
]


def refused(payload: str) -> bool:
    return "$$" in payload or any(ord(c) < 32 or ord(c) == 127 for c in payload)


def test_every_name_renderer_yields_exactly_one_statement_for_every_name_payload(monkeypatch):
    payloads_in(monkeypatch, NAME_PAYLOADS)
    out = run_macro("it_name_renderers", {})
    errors = out.pop("refused")
    assert set(out) >= {"procedure_guarded_call", "columns_block", "try_show", "try_scalar", "guarded_ddl",
                        "relation_quoted_name", "schema_quoted_name", "changes_source", "body"}
    for payload, error in zip(NAME_PAYLOADS, errors):
        assert bool(error) == refused(payload), (payload, error)
    for renderer, statements in out.items():
        assert len(statements) == len(NAME_PAYLOADS)
        for payload, sql in zip(NAME_PAYLOADS, statements):
            if refused(payload):
                assert sql is None, (renderer, payload)
            else:
                assert len(split(sql)) == 1, (renderer, payload, sql[:200])
                # a `$$` body holds exactly its own two delimiters; everything else carries no dollar sign
                owns_body = renderer in ("procedure_guarded_call", "body")
                assert sql.count("$") == (4 if owns_body else 0), (renderer, payload, sql[:200])


# --- a valid database name that is not a plain identifier (MY-DB, "My DB") -----------------------------------------

@pytest.mark.parametrize("database,expected", [
    ("MY-DB", '"MY-DB"."ICECORE_OBS"."ICECORE_EMIT"'),
    ("My DB", '"MY DB"."ICECORE_OBS"."ICECORE_EMIT"'),
    ('A"B', '"A""B"."ICECORE_OBS"."ICECORE_EMIT"'),
])
def test_the_procedure_of_a_database_with_a_hyphen_a_space_or_a_quote_renders_and_passes_the_guard(monkeypatch, database, expected):
    monkeypatch.setenv("SNOWFLAKE_DATABASE", database)
    out = run_macro("it_procedure_for_target", {})
    assert out["error"] is None and out["procedure"] == expected
    assert f"CALL {expected}(PARSE_JSON(" in out["sql"]
    assert len(split(out["sql"])) == 1


@pytest.mark.parametrize("database", ["MY-DB", "My DB"])
def test_emit_renders_for_a_database_with_a_hyphen_or_a_space(monkeypatch, database):
    monkeypatch.setenv("SNOWFLAKE_DATABASE", database)
    out = run_macro("it_emit_render", {"events": [{"kind": "metric", "message": "m"}]})
    assert f'"{database.upper()}"."ICECORE_OBS"."ICECORE_EMIT"' in out["sql"]


@pytest.mark.parametrize("database", ["MY$$DB", "MY\nDB"])
def test_emit_on_a_hostile_database_warns_and_skips_and_never_raises(monkeypatch, database):
    # Offline: a query would fail with a connection error, so a clean result proves nothing was sent.
    monkeypatch.setenv("SNOWFLAKE_DATABASE", database)
    out, logs = run_macro_with_logs("it_emit_events", {"events": [{"kind": "metric"}]})
    assert out == {"events": 0, "calls": 0, "statuses": []}
    assert any("telemetry not written" in line and "invalid SQL name" in line for line in logs)
    out, logs = run_macro_with_logs("it_emit_run_hook", {"items": [{"type": "log", "message": "m"}]})
    assert out["calls"] == 0
    assert any("telemetry not written" in line for line in logs)


def test_the_guarded_status_of_a_bad_procedure_is_a_status_not_an_exception():
    out = call_macro("_icecore_guarded_status", procedure='P$$; GRANT x', items=[])
    assert out.startswith("icecore-error invalid_procedure")


@pytest.mark.parametrize("procedure", ['"A"."B"."C"', '"MY$DB"."S"."P"', '"MY-DB"."S"."P"', '"My DB"."S"."P"', '"A""B"."S"."P"'])
def test_the_guard_accepts_every_name_icecore_sql_builds(procedure):
    assert call_macro("_icecore_procedure_problem", procedure=procedure) is None


@pytest.mark.parametrize("procedure", ['A.B.C', '"A"."B"."C', '"A"B"."S"."P"', '"A$$"."S"."P"', '"A\nB"."S"."P"', '"A"."S";"P"', '', '"A".'])
def test_the_guard_refuses_what_is_not_a_chain_of_quoted_names(procedure):
    assert call_macro("_icecore_procedure_problem", procedure=procedure)


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

HAND_QUOTING = [
    # a quote glued to a value or a call: '"' ~ x, x ~ '"', '."' ~ x ~ '"', "'" ~ x ~ "'"
    re.compile(r"""'\.?"'\s*~|~\s*'"\.?'|'[^']*\."'\s*~|~\s*'"[^']*'"""),
    re.compile(r"""["]'["]\s*~|~\s*["]'["]"""),
    # replace of a quote: replace("'", ...), replace('"', '""'), replace("\\'" ...)
    re.compile(r"""replace\(\s*(['"])(\\\\|'|"|\\')\1\s*,\s*(['"])(?!\3)"""),
    re.compile(r"""replace\(\s*["]'["]"""),
    # the retired helper names are wrappers: no caller uses them for a literal
    re.compile(r"icecore\._(icecore_sql_literal|icecore_block_literal|icecore_schema_literal)\("),
    re.compile(r"\bset\s+\w+\s*=\s*icecore\._icecore_(sql_literal|block_literal|schema_literal)\b"),
]


def test_only_icecore_sql_builds_literals_and_identifiers():
    # No macro builds a quoted identifier or a string literal by hand: not '."' ~ x ~ '"', not '"' ~ x ~ '"', not
    # "'" ~ x ~ "'", no replace of a quote, no call of the retired literal wrappers (macros/util/sql.sql is the one place).
    offenders = []
    for path in (REPO / "macros").rglob("*.sql"):
        if path.name == "sql.sql":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("{#-")[0]
            # dropping quotes to compare two names (replace('"', '')) builds no SQL
            code = code.replace("""replace('"', '')""", "")
            if "raise_compiler_error" in code or re.search(r"\blog\(", code):
                continue  # a message quoting a value for a person builds no SQL
            if any(rx.search(code) for rx in HAND_QUOTING):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, offenders


@pytest.mark.parametrize("line", [
    """{{ return('"' ~ db ~ '"."' ~ name ~ '"') }}""",
    """{{ return(schema ~ '."' ~ view['name'] ~ '"') }}""",
    """{{ '"' ~ (parts[0] | replace('"', '""')) ~ '"' }}""",
    """{%- do quoted.append('"' ~ part ~ '"') -%}""",
    """{%- set lit = "'" ~ value ~ "'" -%}""",
    """{{ value | replace("'", "''") }}""",
    """{{ icecore._icecore_sql_literal(x) }}""",
])
def test_the_scan_catches_every_hand_quoting_pattern(line):
    assert any(rx.search(line.replace("""replace('"', '')""", "")) for rx in HAND_QUOTING), line
