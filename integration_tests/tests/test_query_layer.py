"""The app's query layer, streamlit/lib/queries.py: one typed function per icecore view, bound parameters, a per-page-session cache.

Offline tests (no Snowflake, no Streamlit needed) run the builders and the cache against a fake session that returns
fixture rows. The sandbox test creates the views with icecore.create_views in ICECORE_OBS_<run_id>, seeds metric and
schema_change records, and reads them back through the query functions over a qmark-bound connection.

Merged views (status `merged` in VIEWS): METRICS, SCHEMA_CHANGES, TEST_RESULTS, LINEAGE, LINEAGE_LATEST,
CAPABILITIES, INVOCATIONS, NODE_EXECUTIONS, SILENCE_EVENTS. Their columns are checked against the SQL that icecore.create_views renders
(offline), and the sandbox test reads every one of them. ALERTS_FEED (#57) is coming: its
function returns no rows and runs no query.

The sandbox test binds with the connector's qmark paramstyle (QmarkSession below), not with a Snowpark
`session.sql(sql, params=[...])`: Snowpark is only installed inside Streamlit in Snowflake, not in the test venvs, and
both bind the same `?` placeholders on the server, so queries.py uses `session.sql(sql, params=...)` and the test
supplies a session with that one method.
"""
import ast
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from helpers.events import wait_for_events
from helpers.macros import run_macro

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "streamlit"
sys.path.insert(0, str(APP_DIR))
from lib import queries  # noqa: E402  (no Streamlit import at module level)

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
EVIL = "x'; drop table t; --"

BUILDERS = {
    "METRICS": lambda s: queries.build_metrics(s, relation=EVIL, metric="m", column="c", dimension="d",
                                               since="2026-01-01 00:00:00", until=datetime(2026, 2, 1)),
    "SCHEMA_CHANGES": lambda s: queries.build_schema_changes(s, relation=EVIL, change_kind="added", since="2026-01-01"),
    "TEST_RESULTS": lambda s: queries.build_test_results(s, EVIL, ["fail", "error"], "inv", "2026-01-01"),
    "LINEAGE": lambda s: queries.build_lineage(s, EVIL, "p"),
    "LINEAGE_LATEST": lambda s: queries.build_lineage_latest(s, EVIL),
    "CAPABILITIES": lambda s: queries.build_capabilities(s),
    "INVOCATIONS": lambda s: queries.build_invocations(s, ["success", EVIL], "2026-01-01"),
    "NODE_EXECUTIONS": lambda s: queries.build_node_executions(s, EVIL, ["error"]),
    "ALERTS_FEED": lambda s: queries.build_alerts_feed(s, False, "error", EVIL, "2026-01-01"),
    "SILENCE_EVENTS": lambda s: queries.build_silence_events(s, EVIL, "2026-01-01"),
}


# --- offline: builders ----------------------------------------------------------------------------

def test_one_builder_per_view():
    assert set(BUILDERS) == set(queries.VIEWS)


@pytest.mark.parametrize("view", list(BUILDERS))
def test_values_are_bound_never_formatted(view):
    sql, params = BUILDERS[view]("ICECORE_OBS")
    assert EVIL in params or view in ("CAPABILITIES",)
    assert "drop table" not in sql and "'" not in sql  # no literal in the SQL text at all
    assert sql.count("?") == len(params)
    assert params[-1] >= 1  # the last parameter is the limit


@pytest.mark.parametrize("view", list(BUILDERS))
def test_each_query_reads_one_view_in_the_views_schema(view):
    sql, _ = BUILDERS[view]("my_db.icecore_obs")
    assert re.findall(r"\bfrom\s+(\S+)", sql) == [f"MY_DB.ICECORE_OBS.{view}"]
    assert "select *" not in sql and "base_events" not in sql.lower()


MERGED = ["METRICS", "SCHEMA_CHANGES", "TEST_RESULTS", "LINEAGE", "LINEAGE_LATEST", "CAPABILITIES", "INVOCATIONS",
          "NODE_EXECUTIONS", "SILENCE_EVENTS"]


def _output_columns(sql: str) -> list[str]:
    """Output column names (lower case, quotes removed) of the last top-level select of a rendered view."""
    start = [m.end() for m in re.finditer(r"^select\s*$", sql, re.M)][-1]
    items, depth, current, i = [], 0, "", start
    while i < len(sql):
        ch = sql[i]
        if ch in "'\"":  # a quoted string or identifier is copied whole
            j = sql.index(ch, i + 1)
            current += sql[i:j + 1]
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(current)
            current = ""
            i += 1
            continue
        elif depth == 0 and re.match(r"\s+from\s", sql[i:i + 7]):
            break
        current += ch
        i += 1
    items.append(current)
    names = []
    for item in items:
        item = item.strip()
        m = re.search(r'\bas\s+("[^"]+"|\w+)\s*$', item, re.I | re.S) or re.search(r'("[^"]+"|\w+)\s*$', item)
        names.append(m.group(1).strip('"').lower())
    return names


@pytest.fixture(scope="module")
def rendered_views():
    """The views as icecore.create_views would create them (rendered offline, nothing runs)."""
    return {v["name"]: v["sql"] for v in run_macro("it_views_render", {"schema": "ICECORE_OBS"})}


def test_every_merged_view_is_marked_merged_and_the_others_are_not():
    assert sorted(v for v, spec in queries.VIEWS.items() if spec[0] == "merged") == sorted(MERGED)
    assert queries.VIEWS["ALERTS_FEED"][0] == "pr"


def test_the_rendered_create_views_list_is_the_merged_views(rendered_views):
    assert sorted(rendered_views) == sorted(MERGED)


@pytest.mark.parametrize("view", MERGED)
def test_columns_of_merged_views_are_the_columns_setup_creates(view, rendered_views):
    assert _output_columns(rendered_views[view]) == list(queries.VIEWS[view][2])


def test_the_column_keyword_is_selected_quoted_and_read_as_column():
    sql, _ = queries.build_metrics("S", column="amount")
    assert '"COLUMN" as "column"' in sql and '"COLUMN" = ?' in sql


@pytest.mark.parametrize("schema", ["a b", "a;b", 'a"b', "", "a.b.c", "1abc", "a-b", "a.b; drop"])
def test_bad_schema_names_are_rejected(schema):
    with pytest.raises(ValueError):
        queries.build_metrics(schema)


def test_unknown_view_is_rejected():
    with pytest.raises(ValueError):
        queries.qualified("S", "EVENTS")


@pytest.mark.parametrize("limit", [0, -1, "10", True, 1.5])
def test_limit_must_be_a_positive_integer(limit):
    with pytest.raises(ValueError):
        queries.build_metrics("S", limit=limit)


def test_limit_is_capped():
    assert queries.build_metrics("S", limit=10**9)[1][-1] == queries.MAX_LIMIT


def test_timestamps_bind_as_iso_text():
    sql, params = queries.build_metrics("S", since=datetime(2026, 1, 2, 3, 4, 5), until="2026-02-01")
    assert "observed_at >= to_timestamp_ntz(?)" in sql and "observed_at < to_timestamp_ntz(?)" in sql
    assert params[:2] == ["2026-01-02 03:04:05", "2026-02-01"]


def test_alerts_feed_acknowledged_filter_adds_no_parameter():
    assert "acknowledged_at is null" in queries.build_alerts_feed("S", acknowledged=False)[0]
    assert "acknowledged_at is not null" in queries.build_alerts_feed("S", acknowledged=True)[0]
    assert "acknowledged_at" not in queries.build_alerts_feed("S")[0].split(" from ")[1]


# --- offline: layer, cache, source rules -----------------------------------------------------------

class FakeRow:
    def __init__(self, data):
        self.data = data

    def as_dict(self):
        return dict(self.data)


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or [{"RELATION": "r", "METRIC": "row_count", "VALUE": 1.0}]
        self.calls = []

    def sql(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def collect(self):
        return [FakeRow(r) for r in self.rows]


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def layer(session=None, ttl=60, clock=None):
    return queries.QueryLayer(session or FakeSession(), "ICECORE_OBS", ttl_s=ttl, clock=clock or Clock())


def test_rows_come_back_as_lower_case_dicts():
    assert queries.metrics(layer=layer()) == [{"relation": "r", "metric": "row_count", "value": 1.0}]


def test_the_same_call_twice_is_served_from_cache():
    session = FakeSession()
    lay = layer(session)
    first = queries.metrics(relation="r", layer=lay)
    second = queries.metrics(relation="r", layer=lay)
    assert first == second and len(session.calls) == 1
    queries.metrics(relation="other", layer=lay)  # other parameters: a new query
    assert len(session.calls) == 2


def test_the_cache_expires_after_the_ttl():
    session, clock = FakeSession(), Clock()
    lay = layer(session, ttl=30, clock=clock)
    queries.schema_changes(layer=lay)
    clock.now = 29
    queries.schema_changes(layer=lay)
    assert len(session.calls) == 1
    clock.now = 31
    queries.schema_changes(layer=lay)
    assert len(session.calls) == 2


def test_a_caller_cannot_poison_the_cache():
    lay = layer()
    queries.metrics(layer=lay)[0]["value"] = 999
    assert queries.metrics(layer=lay)[0]["value"] == 1.0


def test_clear_cache_forces_a_new_query():
    session = FakeSession()
    lay = layer(session)
    queries.metrics(layer=lay)
    queries.clear_cache(lay)
    queries.metrics(layer=lay)
    assert len(session.calls) == 2


def test_the_session_receives_bound_params():
    session = FakeSession()
    queries.test_results(statuses=["fail", "error"], layer=layer(session))
    sql, params = session.calls[0]
    assert sql.count("?") == 3 and params == ["fail", "error", queries.DEFAULT_LIMIT]


def test_every_merged_view_function_runs_against_the_fake_session():
    lay = layer()
    for fn in (queries.metrics, queries.schema_changes, queries.test_results, queries.lineage, queries.lineage_latest,
               queries.capabilities, queries.invocations, queries.node_executions):
        assert fn(layer=lay) == [{"relation": "r", "metric": "row_count", "value": 1.0}], fn.__name__


def test_a_view_that_is_not_merged_yet_returns_no_rows_and_runs_no_query():
    session = FakeSession()
    lay = layer(session)
    assert queries.alerts_feed(layer=lay) == []
    assert session.calls == []
    assert not queries.is_available("ALERTS_FEED") and queries.is_available("METRICS")


def test_expired_entries_are_evicted_not_kept():
    session, clock = FakeSession(), Clock()
    lay = layer(session, ttl=30, clock=clock)
    queries.metrics(relation="a", layer=lay)
    queries.metrics(relation="b", layer=lay)
    assert len(lay.cache) == 2
    clock.now = 31
    queries.metrics(relation="c", layer=lay)  # a and b expired: only the new entry is left
    assert len(lay.cache) == 1


def _strings(path: Path) -> list[str]:
    """String constants of a module, docstrings left out."""
    tree = ast.parse(path.read_text())
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node, clean=False):
            doc_ids.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_ids]


def test_the_query_layer_never_names_the_event_table():
    for text in _strings(APP_DIR / "lib" / "queries.py"):
        for forbidden in ("base_events", "TELEMETRY", "RECORD_ATTRIBUTES", "RESOURCE_ATTRIBUTES", "snow.", "ICECORE_EMIT"):
            assert forbidden.lower() not in text.lower(), (forbidden, text)


def test_pages_and_the_other_libs_hold_no_sql_and_no_session():
    files = [*sorted((APP_DIR / "pages").glob("*.py")), APP_DIR / "streamlit_app.py",
             *[p for p in sorted((APP_DIR / "lib").glob("*.py")) if p.name not in ("queries.py", "settings.py")]]
    for path in files:
        source = path.read_text()
        for forbidden in ("get_active_session", "session.sql", "snowflake.snowpark", "snowflake.connector",
                          "base_events", "EVENT_TABLE"):
            assert forbidden not in source, (path.name, forbidden)
        for text in _strings(path):
            assert not re.search(r"\b(select|insert|update|delete|merge)\b.*\bfrom\b|\bfrom\s+\w+\.\w+", text, re.I), (path.name, text)


# --- sandbox --------------------------------------------------------------------------------------

class QmarkSession:
    """The slice of Snowpark that queries.py uses, over a connector connection bound with qmark."""

    def __init__(self, conn):
        from snowflake.connector import DictCursor
        self.cur = conn.cursor(DictCursor)

    def sql(self, sql, params=None):
        self.cur.execute(sql, params or [])
        self._rows = self.cur.fetchall()
        return self

    def collect(self):
        return self._rows


@pytest.fixture(scope="module")
def seeded(dbt, sf):
    import snowflake.connector
    run_id = uuid.uuid4().hex
    schema = f"ICECORE_OBS_{run_id}".upper()
    obs_vars = ["--vars", json.dumps({"icecore": {"obs_schema": schema}})]
    cur = sf.cursor()
    cur.execute(f'create schema "{DATABASE}"."{schema}"')
    conn = None
    try:
        run_macro("it_create_emit_procedures", {}, obs_vars)
        since = cur.execute("select dateadd(minute, -2, convert_timezone('UTC', current_timestamp())::timestamp_ntz)"
                            ).fetchone()[0]
        rel = {"database": DATABASE, "schema": "SPIKES", "identifier": f"it_ql_{run_id}", "quoted": True}
        qualified = f'"{DATABASE}"."SPIKES"."it_ql_{run_id}"'
        run_macro("it_emit_metric", {"rel": rel, "metric": "row_count", "value": 100}, obs_vars)
        run_macro("it_emit_metric", {"rel": rel, "metric": "null_rate", "value": 0.02, "column": "amount",
                                     "dimension": "country=FR"}, obs_vars)
        run_macro("it_emit_events", {"events": [{"kind": "schema_change", "attributes": {
            "dbt.node.relation": qualified, "dbt.schema.change_kind": "added", "dbt.schema.column": "NEW_COL",
            "dbt.schema.to_type": "NUMBER(38,0)"}}]}, obs_vars)
        wait_for_events(
            sf, "resource_attributes:\"snow.database.name\"::string = %(db)s"
                " and resource_attributes:\"snow.schema.name\"::string = %(schema)s and record_type = 'SPAN_EVENT'",
            {"db": DATABASE, "schema": schema}, min_rows=3, since=since, settle_s=5)
        run_macro("it_create_views", {"schema": schema}, obs_vars)
        conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
            private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], role=os.environ["SNOWFLAKE_ROLE"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"], database=DATABASE, paramstyle="qmark")
        lay = queries.QueryLayer(QmarkSession(conn), f"{DATABASE}.{schema}")
        yield {"layer": lay, "qualified": qualified, "since": since}
    finally:
        if conn is not None:
            conn.close()
        cur.execute(f'drop schema if exists "{DATABASE}"."{schema}" cascade')


@pytest.mark.sandbox
def test_metrics_read_through_the_real_view_with_bound_parameters(seeded):
    rows = queries.metrics(relation=seeded["qualified"], since=seeded["since"], layer=seeded["layer"])
    assert sorted((r["metric"], r["value"], r["column"], r["dimension"]) for r in rows) == [
        ("null_rate", 0.02, "amount", "country=FR"), ("row_count", 100.0, None, None)]
    assert set(rows[0]) == set(queries.VIEWS["METRICS"][2])


@pytest.mark.sandbox
def test_a_filter_with_a_quote_is_just_a_value(seeded):
    assert queries.metrics(relation=EVIL, layer=seeded["layer"]) == []
    (row,) = queries.metrics(relation=seeded["qualified"], metric="row_count", layer=seeded["layer"])
    assert row["value"] == 100.0


@pytest.mark.sandbox
def test_schema_changes_read_through_the_real_view(seeded):
    (row,) = queries.schema_changes(relation=seeded["qualified"], layer=seeded["layer"])
    assert (row["change_kind"], row["column"], row["from_type"], row["to_type"]) == (
        "added", "NEW_COL", None, "NUMBER(38,0)")
    assert set(row) == set(queries.VIEWS["SCHEMA_CHANGES"][2])


@pytest.mark.sandbox
@pytest.mark.parametrize("view", MERGED)
def test_every_merged_view_is_readable_with_the_columns_the_app_selects(view, seeded):
    """The select list names every column of VIEWS[view]: an unknown column fails the query. No other rows exist in
    the run schema for most views, so the shape is checked on the rows that do come back."""
    fn = getattr(queries, view.lower())
    rows = fn(layer=seeded["layer"])
    assert isinstance(rows, list)
    for row in rows:
        assert set(row) == set(queries.VIEWS[view][2])


@pytest.mark.sandbox
def test_the_second_call_is_served_from_cache_on_the_real_session(seeded):
    lay = seeded["layer"]
    lay.clear()
    seen = []
    real = lay.session.sql
    lay.session.sql = lambda sql, params=None: seen.append(sql) or real(sql, params)
    try:
        queries.metrics(relation=seeded["qualified"], layer=lay)
        queries.metrics(relation=seeded["qualified"], layer=lay)
    finally:
        lay.session.sql = real
    assert len(seen) == 1
