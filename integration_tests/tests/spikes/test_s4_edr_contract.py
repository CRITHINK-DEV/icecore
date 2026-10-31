"""Spike S4: capture what `edr report` reads, so the Elementary-compatible views (Phase 6) have a fixed target.

Runs the probe project integration_tests/elementary_probe/ (two models, one Elementary test) in its own
schema ICECORE_SANDBOX.ELEMENTARY_PROBE_<run_id>, then `edr report`, then reads edr's queries back from
INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION and records {relation: [columns]} in spikes/results/s4.json
and the raw queries in spikes/results/s4_edr_queries.sql. The schema is dropped at the end.

S4 only: Elementary is not in the requirements files. Install it in a venv of its own and run this test
with that venv (it also needs pytest):

    cd integration_tests
    python3.13 -m venv elementary_probe/.venv
    elementary_probe/.venv/bin/pip install 'elementary-data[snowflake]==0.26.1' pytest
    source ~/.snowflake/icecore_ci.env
    elementary_probe/.venv/bin/pytest -m sandbox tests/spikes/test_s4_edr_contract.py -v

Without `edr` next to the running interpreter the test is skipped (CI's sandbox job does not install it).
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers.findings import RESULTS_DIR, record_finding

IT_DIR = Path(__file__).resolve().parents[2]
PROBE_DIR = IT_DIR / "elementary_probe"
EDR = Path(sys.executable).parent / "edr"
DBT = Path(sys.executable).parent / "dbt"
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX")
SCHEMA_PREFIX = "ELEMENTARY_PROBE_"
SCHEMA_PLACEHOLDER = "<ELEMENTARY_SCHEMA>"

# "DB"."ELEMENTARY_PROBE_x"."T", DB.ELEMENTARY_PROBE_x.T, ELEMENTARY_PROBE_x.T (any case, quoted or not)
RELATION_RE = re.compile(
    r'(?:"?[A-Za-z0-9_$]+"?\s*\.\s*)?"?(' + SCHEMA_PREFIX + r'[A-Za-z0-9_]+)"?\s*\.\s*"?([A-Za-z0-9_$]+)"?',
    re.IGNORECASE,
)


# --- capture -----------------------------------------------------------------------------------------

def edr_queries(sf, session_id: int) -> list[dict]:
    """Every query of one session, oldest first: {query_id, query_type, start_time, query_text}."""
    cur = sf.cursor()
    cur.execute(
        "select query_id, query_type, start_time, query_text from table(information_schema.query_history_by_session("
        "session_id => %s, result_limit => 10000)) order by start_time",
        (int(session_id),),
    )
    return [{"query_id": q, "query_type": t, "start_time": st, "query_text": s} for q, t, st, s in cur.fetchall()]


def relation_columns(sf, schema: str) -> dict[str, dict[str, str]]:
    """{relation: {column: data_type}} for every table and view of the probe schema, lowercased, in column order."""
    cur = sf.cursor()
    cur.execute(
        f"select lower(table_name), lower(column_name), data_type from {DATABASE}.information_schema.columns "
        "where table_schema = %s order by table_name, ordinal_position",
        (schema.upper(),),
    )
    out: dict[str, dict[str, str]] = {}
    for table, column, data_type in cur.fetchall():
        out.setdefault(table, {})[column] = data_type
    return out


def relation_kinds(sf, schema: str) -> dict[str, str]:
    """{relation: 'BASE TABLE' | 'VIEW' | ...} in the probe schema."""
    cur = sf.cursor()
    cur.execute(
        f"select lower(table_name), table_type from {DATABASE}.information_schema.tables where table_schema = %s",
        (schema.upper(),),
    )
    return dict(cur.fetchall())


def _is_probe(table) -> bool:
    return table.db.upper().startswith(SCHEMA_PREFIX)


def _trace(scope, column, exp, Scope, depth: int = 0) -> set[str]:
    """Probe relations a column passes through unchanged (renames allowed), across CTEs, subqueries and unions.

    A computed column (CASE, arithmetic, ...) traces to nothing: a filter on it is not a filter on the relation.
    """
    if depth > 50:
        return set()
    source = scope.sources.get(column.table)
    if isinstance(source, exp.Table):
        return {source.name.lower()} if _is_probe(source) else set()
    if not isinstance(source, Scope):
        return set()
    branches = getattr(source, "set_operation_scopes", None) or getattr(source, "union_scopes", None) or [source]
    found: set[str] = set()
    for branch in branches:
        if not isinstance(branch.expression, exp.Select):
            continue
        for projection in branch.expression.selects:
            if projection.alias_or_name.lower() == column.name.lower():
                inner = projection.unalias()
                if isinstance(inner, exp.Column):
                    found |= _trace(branch, inner, exp, Scope, depth + 1)
    return found


def _analyze_sqlglot(sql: str, schema: str, known: dict[str, dict[str, str]]) -> dict | None:
    """Columns each probe relation must supply, and the WHERE filters that restrict its rows.

    Stars are expanded from `known`, then projections nobody uses are pruned (pushdown_projections), so
    `select * from t` inside a CTE counts only the columns the query goes on to use. A WHERE counts for a
    relation when one of its columns reaches the WHERE unchanged, directly or through `select *` CTEs.
    None when sqlglot is missing or cannot handle the query.
    """
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.pushdown_projections import pushdown_projections
        from sqlglot.optimizer.qualify import qualify
        from sqlglot.optimizer.scope import Scope, traverse_scope
    except ImportError:
        return None
    mapping = {DATABASE.lower(): {schema.lower(): {t: dict(cols) for t, cols in known.items()}}}
    try:
        tree = sqlglot.parse_one(sql, read="snowflake")
        if isinstance(tree, exp.Create):
            tree = tree.expression.this if isinstance(tree.expression, exp.Subquery) else tree.expression
        tree = qualify(tree, schema=mapping, dialect="snowflake", validate_qualify_columns=False,
                       quote_identifiers=False, identify=False)
        tree = pushdown_projections(tree)
        columns: dict[str, set[str]] = {}
        filters: dict[str, set[str]] = {}
        for scope in traverse_scope(tree):
            probe = {alias: src for alias, src in scope.sources.items() if isinstance(src, exp.Table) and _is_probe(src)}
            for src in probe.values():
                columns.setdefault(src.name.lower(), set())
            for column in scope.columns:
                if column.table in probe:
                    columns[probe[column.table].name.lower()].add(column.name.lower())
            where = scope.expression.args.get("where") if isinstance(scope.expression, exp.Select) else None
            if where is None:
                continue
            targets: set[str] = set()
            for column in where.this.find_all(exp.Column):
                if column.table in scope.sources:  # columns of a nested subquery belong to its own scope
                    targets |= _trace(scope, column, exp, Scope)
            if targets:
                text = re.sub(schema, SCHEMA_PLACEHOLDER, where.this.sql(dialect="snowflake"), flags=re.IGNORECASE)
                text = re.sub(r"(__TMP_)[0-9]+", r"\1<ts>", text, flags=re.IGNORECASE)
                for rel in targets:
                    filters.setdefault(rel, set()).add(text)
        return {"columns": columns, "filters": filters, "outputs": list(tree.named_selects)}
    except Exception:
        return None


def _columns_tokens(sql: str, relations: set[str], known: dict[str, dict[str, str]]) -> dict[str, set[str]]:
    """Fallback: a relation's known columns that appear as words in the query (an upper bound)."""
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", sql)}
    star = re.search(r"select\s+(?:distinct\s+)?\*", sql, re.IGNORECASE) is not None
    return {rel: set(known.get(rel, {})) if star else {c for c in known.get(rel, {}) if c in words} for rel in relations}


def analyze_edr_queries(queries: list[dict], known: dict[str, dict[str, str]]) -> dict:
    """What a list of edr queries reads and writes in the probe schema.

    Returns {"contract": {relation: [column]}, "types": {relation: {column: type}},
    "filters": {relation: [where]}, "reads": {relation: {"filtered": [query no.], "unfiltered": [query no.]}},
    "writes": [{"query_type", "relation"}], "parse_fallbacks": int}. Query numbers are 1-based positions in
    `queries`, the numbering of spikes/results/s4_edr_queries.sql.
    Relations edr creates itself (its temporary tables) are writes, not part of the contract.
    """
    contract: dict[str, set[str]] = {}
    filters: dict[str, set[str]] = {}
    reads: dict[str, dict[str, list[int]]] = {}
    writes, fallbacks = [], 0
    visible = dict(known)  # plus edr's temporary tables, with the columns of the query that created them
    for number, query in enumerate(queries, 1):
        sql = re.sub(r"/\*.*?\*/", " ", query["query_text"] or "", flags=re.S)  # dbt and Elementary query comments
        qtype = query["query_type"]
        hits = RELATION_RE.findall(sql)
        if not hits or qtype in ("SHOW", "DESCRIBE", "DROP"):
            continue
        schema = hits[0][0]
        created = set()
        if qtype.startswith("CREATE"):
            created = {hits[0][1].lower()}
            writes.append({"query_type": qtype, "relation": hits[0][1].lower()})
        relations = {t.lower() for _, t in hits} - created
        relations = {r for r in relations if r in known}  # edr's own temporary tables are not in the contract
        if not relations:
            continue
        found = _analyze_sqlglot(sql, schema, visible)
        if found is not None and created:
            visible[next(iter(created))] = {c.lower(): "UNKNOWN" for c in found["outputs"]}
        if found is None:
            fallbacks += 1
            found = {"columns": _columns_tokens(sql, relations, known), "filters": {}}
        for rel in relations:
            contract.setdefault(rel, set()).update(found["columns"].get(rel, set()))
            filters.setdefault(rel, set()).update(found["filters"].get(rel, set()))
            kind = "filtered" if found["filters"].get(rel) else "unfiltered"
            reads.setdefault(rel, {"filtered": [], "unfiltered": []})[kind].append(number)
    return {
        "contract": {rel: sorted(cols) for rel, cols in sorted(contract.items())},
        "types": {rel: {c: known[rel][c] for c in sorted(cols)} for rel, cols in sorted(contract.items())},
        "filters": {rel: sorted(f) for rel, f in sorted(filters.items()) if f},
        "reads": {rel: reads[rel] for rel in sorted(reads)},
        "writes": writes,
        "parse_fallbacks": fallbacks,
    }


def capture_edr_contract(sf, session_id: int, known: dict[str, dict[str, str]] | None = None) -> dict[str, list[str]]:
    """{relation: [column, ...]} for every relation of an ELEMENTARY_PROBE_* schema that the session read."""
    queries = edr_queries(sf, session_id)
    if known is None:
        schemas = {m[0] for q in queries for m in RELATION_RE.findall(q["query_text"] or "")}
        known = {k: v for schema in schemas for k, v in relation_columns(sf, schema).items()}
    return analyze_edr_queries(queries, known)["contract"]


def tagged_session_ids(sf, query_tag: str, since: datetime) -> list[int]:
    """Sessions of the current user that ran queries with `query_tag` since `since` (other agents share the user)."""
    cur = sf.cursor()
    cur.execute(
        "select distinct session_id from table(information_schema.query_history_by_user("
        "user_name => current_user(), end_time_range_start => %s::timestamp_ltz, result_limit => 10000)) "
        "where query_tag = %s",
        (since.isoformat(), query_tag),
    )
    return sorted(row[0] for row in cur.fetchall())


# --- probe run -----------------------------------------------------------------------------------------

@pytest.fixture
def probe_schema(sf, run_id):
    """ICECORE_SANDBOX.ELEMENTARY_PROBE_<run_id>: dbt creates it, the test always drops it."""
    schema = f"{SCHEMA_PREFIX}{run_id.upper()}"
    yield schema
    sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


def _run(cmd: list[str], env: dict, ok_codes=(0,)) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=PROBE_DIR, env=env, capture_output=True, text=True, timeout=1800)
    if proc.returncode not in ok_codes:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
        pytest.fail(f"{' '.join(map(str, cmd[:3]))} exited {proc.returncode}:\n{tail}")
    return proc


def _versions(env: dict) -> dict:
    lock = (PROBE_DIR / "package-lock.yml").read_text() if (PROBE_DIR / "package-lock.yml").exists() else ""
    package = re.search(r"elementary-data/elementary\s*\n\s*version:\s*([0-9.]+)", lock)
    cli = subprocess.run([str(EDR), "--version"], cwd=PROBE_DIR, env=env, capture_output=True, text=True).stdout
    dbt = subprocess.run([str(DBT), "--version"], cwd=PROBE_DIR, env=env, capture_output=True, text=True).stdout
    return {
        "elementary_cli": (re.search(r"([0-9]+\.[0-9]+\.[0-9]+)", cli) or [None, None])[1],
        "elementary_dbt_package": package[1] if package else None,
        "dbt_core": (re.search(r"Core:\s*\n\s*- installed:\s*([0-9.]+)", dbt) or [None, None])[1],
        "dbt_snowflake": (re.search(r"snowflake:\s*([0-9.]+)", dbt) or [None, None])[1],
    }


def _write_queries(queries: list[dict], schema: str) -> None:
    lines = [
        "-- Spike S4: every query `edr report` ran (QUERY_HISTORY_BY_SESSION), in order.",
        f"-- The per-run schema is replaced by {SCHEMA_PLACEHOLDER}. Captured by tests/spikes/test_s4_edr_contract.py.",
        "",
    ]
    for i, q in enumerate(queries, 1):
        text = re.sub(schema, SCHEMA_PLACEHOLDER, (q["query_text"] or "").strip(), flags=re.IGNORECASE)
        lines += [f"-- [{i}] {q['query_type']}", text.rstrip(";") + ";", ""]
    (RESULTS_DIR / "s4_edr_queries.sql").write_text("\n".join(lines))


@pytest.mark.sandbox
def test_s4_capture_edr_contract(sf, run_id, probe_schema, tmp_path):
    if not EDR.exists():
        pytest.skip("S4 only: run with a venv that has elementary-data[snowflake] (see this module's docstring)")

    edr_tag, dbt_tag = f"icecore_s4_edr_{run_id}", f"icecore_s4_dbt_{run_id}"
    env = {**os.environ, "ICECORE_EDR_SCHEMA": probe_schema, "ICECORE_EDR_QUERY_TAG": edr_tag,
           "ICECORE_DBT_QUERY_TAG": dbt_tag, "DO_NOT_TRACK": "1"}
    dirs = ["--project-dir", str(PROBE_DIR), "--profiles-dir", str(PROBE_DIR)]
    config_dir = tmp_path / "edr_config"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("anonymous_usage_tracking: false\n")

    committed_path = RESULTS_DIR / "s4.json"
    committed = json.loads(committed_path.read_text()) if committed_path.exists() else {}

    _run([str(DBT), "deps"] + dirs, env)
    _run([str(DBT), "run", "--select", "elementary"] + dirs, env)
    _run([str(DBT), "build"] + dirs, env)

    started = datetime.now(timezone.utc)
    report = tmp_path / "report.html"
    _run([str(EDR), "report", "--file-path", str(report), "--open-browser", "false",
          "--profiles-dir", str(PROBE_DIR), "--project-dir", str(PROBE_DIR), "--config-dir", str(config_dir),
          "--target-path", str(tmp_path / "edr_target")], env)
    assert report.exists() and report.stat().st_size > 10_000, "edr report wrote no report"

    # Query history fills within seconds; poll until edr's sessions stop gaining queries.
    known = relation_columns(sf, probe_schema)
    deadline, previous, queries, sessions = time.time() + 120, -1, [], []
    while time.time() < deadline:
        sessions = tagged_session_ids(sf, edr_tag, started)
        queries = [q for s in sessions for q in edr_queries(sf, s)]
        if queries and len(queries) == previous:
            break
        previous = len(queries)
        time.sleep(10)
    assert sessions, f"no session ran with query tag {edr_tag}"

    queries.sort(key=lambda q: q["start_time"])
    result = analyze_edr_queries(queries, known)
    contract = result["contract"]
    kinds = relation_kinds(sf, probe_schema)

    _write_queries(queries, probe_schema)
    record_finding("s4", "contract", contract)
    record_finding("s4", "types", result["types"])
    record_finding("s4", "filters", result["filters"])
    record_finding("s4", "reads", result["reads"])  # query numbers of s4_edr_queries.sql
    record_finding("s4", "probe_schema_columns", known)  # every relation in the schema, for offline replays
    record_finding("s4", "writes", result["writes"])
    record_finding("s4", "relation_kinds", {rel: kinds.get(rel) for rel in contract})
    described = sorted({t.lower() for q in queries if q["query_type"] == "DESCRIBE"
                        for _, t in RELATION_RE.findall(q["query_text"] or "")})
    record_finding("s4", "described", described)  # edr runs DESCRIBE TABLE on these before selecting
    record_finding("s4", "versions", _versions(env))
    record_finding("s4", "edr_run", {
        "sessions": len(sessions), "queries": len(queries), "parse_fallbacks": result["parse_fallbacks"],
        "query_types": {t: sum(1 for q in queries if q["query_type"] == t) for t in sorted({q["query_type"] for q in queries})},
        "probe_relations": sorted(known), "captured_at": started.isoformat(),
    })

    # Drift: the capture must equal the committed one. ICECORE_S4_RECAPTURE=1 records a new baseline instead.
    if committed and os.environ.get("ICECORE_S4_RECAPTURE") != "1":
        captured = {"contract": contract, "types": result["types"], "filters": result["filters"],
                    "described": described, "relation_kinds": {rel: kinds.get(rel) for rel in contract}}
        for key, value in captured.items():
            assert value == committed.get(key), f"edr report's {key} drifted from spikes/results/s4.json"

    # Measured behaviour of edr report 0.26.1 (spikes/S4.md).
    assert result["parse_fallbacks"] == 0, "every edr query parsed, so the columns are exact, not an upper bound"
    assert {"dbt_invocations", "elementary_test_results", "dbt_run_results", "dbt_tests", "dbt_models",
            "dbt_sources", "model_run_results"} <= set(contract)
    assert {"invocation_id", "command"} <= set(contract["dbt_invocations"])
    assert {"test_unique_id", "status", "detected_at"} <= set(contract["elementary_test_results"])
    windows = {"elementary_test_results": "detected_at", "dbt_run_results": "execute_completed_at",
               "dbt_source_freshness_results": "generated_at", "test_result_rows": "detected_at",
               "model_run_results": "generated_at"}
    for rel, column in windows.items():
        assert any(column in f.lower() and "< 7" in f for f in result["filters"][rel]), f"7-day window on {rel}.{column}"
    assert result["reads"]["model_run_results"]["filtered"], "window applied over a select * CTE (query [27])"
    temp_writes = [w for w in result["writes"] if w["query_type"] == "CREATE_TABLE_AS_SELECT"]
    assert temp_writes and all(w["relation"].startswith("ordered_test_results__tmp") for w in temp_writes), \
        "edr report creates a temporary table in the Elementary schema"


# --- offline checks of the parser ---------------------------------------------------------------------

def test_relation_regex_finds_quoted_and_bare_names():
    sql = ('select a.x from "ICECORE_SANDBOX"."ELEMENTARY_PROBE_AB12"."DBT_INVOCATIONS" a '
           "join icecore_sandbox.elementary_probe_ab12.elementary_test_results r on 1=1")
    assert [t.lower() for _, t in RELATION_RE.findall(sql)] == ["dbt_invocations", "elementary_test_results"]


def test_token_fallback_is_bounded_by_known_columns():
    known = {"dbt_invocations": {"invocation_id": "TEXT", "run_started_at": "TEXT", "job_id": "TEXT"}}
    cols = _columns_tokens("select invocation_id, run_started_at from x", {"dbt_invocations"}, known)
    assert cols == {"dbt_invocations": {"invocation_id", "run_started_at"}}


def test_analyzer_skips_metadata_queries_and_records_writes():
    known = {"dbt_invocations": {"invocation_id": "TEXT", "command": "TEXT", "job_id": "TEXT"}}
    queries = [
        {"query_type": "SHOW", "query_text": "show objects in ICECORE_SANDBOX.ELEMENTARY_PROBE_AB"},
        {"query_type": "CREATE_TABLE_AS_SELECT", "query_text":
            "create temporary table ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.tmp_x as (select invocation_id "
            "from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.dbt_invocations where command = 'build')"},
    ]
    result = analyze_edr_queries(queries, known)
    assert result["writes"] == [{"query_type": "CREATE_TABLE_AS_SELECT", "relation": "tmp_x"}]
    assert set(result["contract"]) == {"dbt_invocations"}
    assert "invocation_id" in result["contract"]["dbt_invocations"]


MRR = {"model_run_results": {"unique_id": "TEXT", "generated_at": "TEXT", "status": "TEXT", "execution_time": "FLOAT"},
       "dbt_run_results": {"unique_id": "TEXT", "execute_completed_at": "TEXT"}}


def test_filter_over_a_star_cte_is_traced_to_its_relation():
    """Review round 1: query [27] filters model_run_results through `with model_runs as (select * ...)`."""
    pytest.importorskip("sqlglot")
    sql = ("with model_runs as (select * from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.model_run_results) "
           "select unique_id, status from model_runs "
           "where datediff(day, cast(generated_at as timestamp), current_timestamp::timestamp) < 7 "
           "and unique_id not like 'model.elementary.%'")
    result = analyze_edr_queries([{"query_type": "SELECT", "query_text": sql}], MRR)
    [where] = result["filters"]["model_run_results"]
    assert "GENERATED_AT" in where.upper() and "MODEL.ELEMENTARY.%" in where.upper()
    assert result["reads"]["model_run_results"] == {"filtered": [1], "unfiltered": []}


def test_filter_on_a_computed_column_is_not_attributed():
    pytest.importorskip("sqlglot")
    sql = ("with r as (select unique_id, case when status = 'x' then 1 else 0 end as flag "
           "from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.model_run_results) select unique_id from r where flag = 1")
    result = analyze_edr_queries([{"query_type": "SELECT", "query_text": sql}], MRR)
    assert "model_run_results" not in result["filters"]
    assert result["reads"]["model_run_results"] == {"filtered": [], "unfiltered": [1]}


def test_reads_say_which_queries_filter_a_relation():
    pytest.importorskip("sqlglot")
    windowed = ("select unique_id from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.dbt_run_results "
                "where datediff(day, cast(execute_completed_at as timestamp), current_timestamp()) < 7")
    plain = "select unique_id from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.dbt_run_results"
    queries = [{"query_type": "SELECT", "query_text": q} for q in (windowed, plain, plain)]
    assert analyze_edr_queries(queries, MRR)["reads"]["dbt_run_results"] == {"filtered": [1], "unfiltered": [2, 3]}


def test_filter_through_a_union_cte_reaches_both_branches():
    pytest.importorskip("sqlglot")
    sql = ("with u as (select unique_id from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.model_run_results "
           "union all select unique_id from ICECORE_SANDBOX.ELEMENTARY_PROBE_AB.dbt_run_results) "
           "select unique_id from u where unique_id like 'model.%'")
    result = analyze_edr_queries([{"query_type": "SELECT", "query_text": sql}], MRR)
    assert set(result["filters"]) == {"model_run_results", "dbt_run_results"}


def test_committed_capture_replays_to_the_committed_contract():
    """The analyzer, run offline over the committed queries and schema, gives the committed contract."""
    pytest.importorskip("sqlglot")
    committed = json.loads((RESULTS_DIR / "s4.json").read_text())
    if "probe_schema_columns" not in committed:
        pytest.skip("s4.json predates probe_schema_columns")
    text = (RESULTS_DIR / "s4_edr_queries.sql").read_text().replace(SCHEMA_PLACEHOLDER, SCHEMA_PREFIX + "REPLAY")
    parts = re.split(r"^-- \[(\d+)\] (\S+)\n", text, flags=re.M)
    queries = [{"query_type": parts[i + 1], "query_text": parts[i + 2]} for i in range(1, len(parts), 3)]
    result = analyze_edr_queries(queries, committed["probe_schema_columns"])
    assert result["parse_fallbacks"] == 0
    for key in ("contract", "types", "filters", "reads"):
        assert result[key] == committed[key], key
