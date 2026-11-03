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
    "emoji 😀 é 漢", "a\x00b", "\x00", "x\x00'; select 1; --",
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
        assert "$" not in literal and "\n" not in literal and "\r" not in literal and "\x00" not in literal
        assert len(split(f"select {literal}")) == 1, payload
        assert len(split(f"CREATE TASK t AS EXECUTE IMMEDIATE $$ BEGIN SELECT {literal}; END; $$")) == 1, payload


def test_string_context_round_trips_the_value(monkeypatch):
    # what Snowflake reads back: \\ \' \x24 \x00 \n \r undone in one pass
    payloads_in(monkeypatch, PAYLOADS)
    out = run_macro("it_sql", {"context": "string"})
    for payload, row in zip(PAYLOADS, out):
        inner = row["sql"][1:-1]
        back = re.sub(r"\\(x24|x00|n|r|\\|')", lambda m: {"x24": "$", "x00": "\x00", "n": "\n", "r": "\r"}.get(m.group(1), m.group(1)), inner)
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


def test_a_value_with_a_nul_renders_a_block_that_still_parses():
    # a raw NUL cuts a Scripting block short: it is written \\x00, and the text holds no NUL at all
    literal = call_macro("_sql", value="a\x00b'; select 1; --", context="string")
    assert "\x00" not in literal and "\\x00" in literal
    block = f"EXECUTE IMMEDIATE $$ BEGIN SELECT {literal}; RETURN 1; END; $$"
    assert "\x00" not in block and len(split(block)) == 1


@pytest.mark.parametrize("warehouse,expected", [("my$wh", "my$wh"), ("MY$WH", "MY$WH"), ("wh_1", "wh_1"), ("My Wh", '"My Wh"'), ("1wh", '"1wh"')])
def test_a_warehouse_with_a_single_dollar_sign_stays_unquoted_and_keeps_its_case(warehouse, expected):
    assert run_macro("it_call", {"name": "_icecore_identifier", "kwargs": {"name": warehouse}}) == expected


def test_an_identifier_with_a_double_dollar_sign_is_refused():
    with pytest.raises(MacroFailed, match="invalid SQL name"):
        run_macro("it_call", {"name": "_icecore_identifier", "kwargs": {"name": "my$$wh"}})


def test_root_cause_on_a_database_with_a_double_dollar_sign_warns_and_skips_and_never_raises(monkeypatch):
    # on-run-end must not raise: reading a view of a database that icecore._sql refuses is a warning and an empty signal
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "MY$$DB")
    failed = [{"unique_id": "test.p.t", "status": "fail", "node": {"unique_id": "test.p.t", "resource_type": "test"}}]
    out, logs = run_macro_with_logs("it_emit_root_cause", {"results": failed, "items": [], "signals": None})
    assert out == []
    assert any("root cause" in line and "invalid SQL name" in line for line in logs), logs


@pytest.mark.parametrize("kind", ["email", "slack", "teams"])
def test_the_default_text_of_an_alert_field_is_a_literal_that_cannot_end_its_string(kind):
    default = "x'); DROP TABLE t; $$ \\' --"
    sql = call_macro("_icecore_alert_field", column="title", type=kind, default=default)
    assert "$" not in sql and len(split(f"select {sql}")) == 1
    assert "coalesce(title, 'x\\')" in sql  # the quote is escaped, not closing the string


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

# a string literal that is only a quote (or a quote with a dot): '"' "'" '\'' "\"" '."' "'."
QUOTE = r"""(?:'(?:"|\\?'|\.")'|"(?:'|\\?"|'\.)")"""
OP = r"[~+]"

HAND_QUOTING = [
    # a quote glued to a value or a call with ~ or +: '"' ~ x, x + '"', "'" ~ x, '\'' ~ x, '."' ~ x
    re.compile(rf"{QUOTE}\s*{OP}|{OP}\s*{QUOTE}"),
    # a quote inside a longer literal next to an operator: '"."' ~ x, x ~ '"."'
    re.compile(r"""'[^']*\."'\s*[~+]|[~+]\s*'"[^']*'"""),
    # a quote in a list that is then joined: ["'", x, "'"] | join
    re.compile(rf"[\[,]\s*{QUOTE}\s*[,\]]"),
    # replace of a quote: replace("'", ...), replace('"', '""'), replace("\'" ...)
    re.compile(r"""replace\(\s*(['"])(\\\\|'|"|\\')\1\s*,\s*(['"])(?!\3)"""),
    re.compile(r"""replace\(\s*["]'["]"""),
    # a {{ }} next to a quote ('{{ x }}', "{{ x }}"): the value is quoted by hand
    re.compile(r"""['"][.]?\s*\{\{|\}\}\s*['"]|\}\}\.'"""),
    # '%s' % x, "'%s'" % x
    re.compile(r"""\\?['"]%s(?!\w)|(?<![\w%])%s\\?['"]"""),
    # str.format
    re.compile(r"(?<!\}')\.format\("),  # '{:,}'.format(n) formats a number for a person
    # a body or its delimiter built with an operator: "$$ " ~ x, x ~ " $$"
    re.compile(r"""\$\$[^'"]*['"]\s*[~+]|[~+]\s*['"][^'"]*\$\$"""),
    # the retired helper names are wrappers: no caller uses them for a literal
    re.compile(r"(?<!macro )(?:icecore\.)?_(icecore_sql_literal|icecore_block_literal|icecore_schema_literal)\("),
    # a bare quote held in a variable: set q = '"'
    re.compile(rf"\bset\s+\w+\s*=\s*{QUOTE}\s*(?:-?%\}}|$)"),
]
# `set literal = icecore._icecore_sql_literal` (also unprefixed, or after a condition): a quoting helper bound to a local
# name without a call, called later by that name
ALIAS = re.compile(r"\bset\s+(\w+)\s*=[^%]*?\b(?:icecore\.)?_\w*(?:literal|quote|identifier|sql)\w*\b(?!\s*\()")
CALL_OF_SQL = re.compile(r"\{\{-?\s*icecore\._sql\([^{}]*\)\s*-?\}\}")
SQL_CALL = "{{SQL}}"  # what a call of icecore._sql leaves behind: still a {{ }}, so a quote around it is seen
# dropping quotes to compare two names builds no SQL: replace(x, '"', '')
DROP_QUOTES = re.compile(rf"replace\([^()]*?,\s*{QUOTE}\s*,\s*(?:''|\"\")\s*\)")
STRING = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\"""")
SEGMENT = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def _blank(text: str) -> str:
    """The text with every character but the newlines turned into a space (line numbers stay right)."""
    return re.sub(r"[^\n]", " ", text)


def strip_comments(text: str) -> str:
    """Only closed {# ... #} comments go; an open one (no `#}`) hides nothing, so code after it is still scanned."""
    return re.sub(r"\{#.*?#\}", lambda m: _blank(m.group(0)), text, flags=re.S)


def strip_messages(text: str) -> str:
    """The argument of log(...), raise_compiler_error(...) and _sla_fail(...) (which raises) goes: a message quoting a value for a person builds no
    SQL. The rest of the line stays checked."""
    out, i = [], 0
    for m in re.finditer(r"\b(?:log|raise_compiler_error|_sla_fail)\(", text):
        if m.start() < i:
            continue
        depth, j, quote = 1, m.end(), None
        while j < len(text) and depth:
            c = text[j]
            if quote:
                if c == "\\":
                    j += 1
                elif c == quote:
                    quote = None
            elif c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        end = j - 1 if depth == 0 else j
        out += [text[i:m.end()], _blank(text[m.end():end])]
        i = end
    return "".join(out) + text[i:]


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def quoted_expressions(code: str) -> set[int]:
    """Lines where, in raw SQL, a `{{ }}` opens inside a single-quoted string ('ICE_{{ x }}', like '%{{ x }}%'): a value
    pasted into a literal by hand. `icecore._sql(...)` calls are `{{SQL}}` here, so a quote around one is caught too."""
    lines, in_quote, pos = set(), False, 0
    for m in SEGMENT.finditer(code):
        raw = code[pos:m.start()]
        i = 0
        while i < len(raw):
            c = raw[i]
            if in_quote and c == "\\":
                i += 1
            elif c == "'":
                in_quote = not in_quote
            elif not in_quote and raw.startswith("--", i):
                i = raw.find("\n", i) if "\n" in raw[i:] else len(raw)
                continue
            i += 1
        if in_quote and m.group(0).startswith("{{"):
            lines.add(_line_of(code, m.start()))
        pos = m.end()
    return lines


def odd_quote_literals(code: str) -> set[int]:
    """Lines where a Jinja string literal holding an odd number of quote characters is joined by ~ or +: the quote it
    opens is closed by another piece, so a value is being quoted by hand ("... = '" ~ v ~ "'")."""
    lines = set()
    for m in SEGMENT.finditer(code):
        seg = m.group(0)
        for lit in STRING.finditer(seg):
            body = lit.group(0)[1:-1].replace("\\'", "'").replace('\\"', '"')
            if (body.count("'") + body.count('"')) % 2 == 0:
                continue
            before, after = seg[:lit.start()].rstrip(), seg[lit.end():].lstrip()
            if before.endswith(("~", "+")) or after.startswith(("~", "+")):
                lines.add(_line_of(code, m.start() + lit.start()))
    return lines


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, line) of every hand-built quote, quoted `{{ }}`, retired wrapper or alias call in a macro file."""
    code = CALL_OF_SQL.sub(lambda m: SQL_CALL + "\n" * m.group(0).count("\n"), strip_messages(strip_comments(text)))
    lines = code.splitlines()
    aliases = {m.group(1) for m in map(ALIAS.search, lines) if m}
    call_of_alias = re.compile(r"(?<![\w.])(?:%s)\s*\(" % "|".join(map(re.escape, aliases))) if aliases else None
    flagged = quoted_expressions(code) | odd_quote_literals(code)
    original = text.splitlines()
    hits = []
    for n, line in enumerate(lines, 1):
        checked = DROP_QUOTES.sub("", line.replace("""replace('"', '')""", ""))
        if (n in flagged or any(rx.search(checked) for rx in HAND_QUOTING) or ALIAS.search(checked)
                or (call_of_alias and call_of_alias.search(checked))):
            hits.append((n, original[n - 1].strip()))
    return hits


def test_only_icecore_sql_builds_literals_and_identifiers():
    # No macro builds a quoted identifier or a string literal by hand (see HAND_QUOTING) or calls a retired wrapper,
    # directly or through an alias (macros/util/sql.sql is the one place).
    offenders = []
    for path in (REPO / "macros").rglob("*.sql"):
        if path.name == "sql.sql":
            continue
        offenders += [f"{path.relative_to(REPO)}:{n}: {line}" for n, line in scan_text(path.read_text())]
    assert not offenders, offenders


@pytest.mark.parametrize("line", [
    """{{ return('"' ~ db ~ '"."' ~ name ~ '"') }}""",
    """{{ return(schema ~ '."' ~ view['name'] ~ '"') }}""",
    """{{ '"' ~ (parts[0] | replace('"', '""')) ~ '"' }}""",
    """{%- do quoted.append('"' ~ part ~ '"') -%}""",
    """{%- set lit = "'" ~ value ~ "'" -%}""",
    """{{ value | replace("'", "''") }}""",
    """{{ icecore._icecore_sql_literal(x) }}""",
    # what the security review of #75 found missing
    """where name = '{{ x }}'""",
    """where name = "{{ x }}" """,
    """where name = '{{ icecore._icecore_identifier(x) }}'""",
    """execute immediate 'create table {{ q }}.'""",
    """from {{ db }}."{{ name }}" """,
    """{%- set q = '"' + x + '"' -%}""",
    """{%- set q = "'%s'" % x -%}""",
    """{%- set q = "'{}'".format(x) -%}""",
    """{%- set q = '\\'' ~ x ~ '\\'' -%}""",
    """{%- set q = ["'", x, "'"] | join -%}""",
    """{%- set q = ['"', x, '"'] | join('') -%}""",
    """{%- set body = "$$ " ~ x -%}""",
    """{%- set body = x ~ " $$" -%}""",
    # an alias of a retired helper (3 Nov: get_metric_history.sql)
    """{%- set literal = icecore._icecore_sql_literal -%}""",
    # a closed comment does not hide the code after it; an open one does not hide it either
    """{#- ok -#} {{ '"' ~ x ~ '"' }}""",
    """{#- never closed {{ '"' ~ x ~ '"' }}""",
    # review of #124: a quote inside a longer literal joined by ~ or +, a quote opened in raw SQL before a {{ }}
    """{%- set e = "coalesce(" ~ column ~ ", '" ~ default ~ "')" -%}""",
    """{{ run_query("select 1 where a = '" ~ v ~ "'") }}""",
    """{{ "a = 'x" + v }}""",
    """where name like '%{{ x }}%'""",
    """where name = 'ICE_{{ x }}'""",
    """where name = '{{ icecore._sql(x, 'string') }}'""",
    """{%- set q = '"' -%}""",
    """{%- set q = "'" -%}""",
    """{{ _icecore_sql_literal(x) }}""",
    """{%- set lit = icecore._icecore_sql_literal if a else b -%}""",
    # the log argument is exempt, the rest of the line is not
    """{% do log('quoting ' ~ x) %}{{ '"' ~ x ~ '"' }}""",
    """{{ exceptions.raise_compiler_error('bad') }}{{ "'" ~ x ~ "'" }}""",
])
def test_the_scan_catches_every_hand_quoting_pattern(line):
    # an open comment hides nothing: the scan of a one-line text is the scan of the file
    assert scan_text(line), line


@pytest.mark.parametrize("helper", ["_icecore_sql_literal", "_icecore_block_literal", "_icecore_identifier"])
def test_the_scan_catches_a_call_of_a_local_alias_of_a_quoting_helper(helper):
    text = "{%- set literal = icecore." + helper + " -%}\nselect {{ literal(x) }}\n"
    assert [n for n, _ in scan_text(text)] == [1, 2]


@pytest.mark.parametrize("line", [
    """{{ icecore._sql(x, 'string') }}""",
    """where name = {{ icecore._sql(x, 'string') }}""",
    """from {{ icecore._sql([db, schema], 'ident') }}""",
    """{#- a '"' ~ x ~ '"' in a comment -#}""",
    """{#- a closed comment -#} {{ icecore._sql(x) }}""",
    """{% do log('icecore: skipped ' ~ x ~ ' ' ~ "'" ~ y ~ "'", info=True) %}""",
    """{{ exceptions.raise_compiler_error("icecore: bad " ~ '"' ~ x ~ '"') }}""",
    """{%- if a.replace('"', '') == b -%}""",
    """{%- set parts = text.split(',') -%}""",
    """{{ return(items | join(', ')) }}""",
    """{%- set rows = run_query(sql).rows -%}""",
    """select 'ICECORE_EMIT' as name, '%' as pct""",
    """where outcome ilike '%success%' and b = 'x' -- it's fine""",
    """where a = 'x' and b = {{ icecore._sql(v, 'string') }} and c = 'y'""",
    """{{ expr | replace(x,'"','') }}""",
    """{%- set text = icecore._icecore_elementary_text -%}""",
    """{%- set error = icecore._sql_error(value, context) -%}""",
    """from {{ obs }}."NODE_EXECUTIONS" as n""",
    """{{ return('{:,}'.format(rows) ~ ' rows') }}""",
])
def test_the_scan_leaves_legitimate_lines_alone(line):
    assert not scan_text(line), line


def test_the_scan_of_a_multi_line_comment_and_log_call_checks_only_what_is_code():
    text = "{#-\n  a '\"' ~ x ~ '\"'\n-#}\n{% do log('a ' ~ '\"' ~ x,\n  info=True) %}\n{{ '\"' ~ y }}\n"
    assert [n for n, _ in scan_text(text)] == [6]
