"""Schema changes page: logic (pure), the column snapshot read, and an AppTest render with the reads patched.

Needs streamlit (the tiles are a console.Tile), so it skips on the dbt venvs. Run it from the Streamlit 1.50 venv:

    cd integration_tests && python -m pytest --noconftest tests/test_app_page_schema.py
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
PAGE = str(APP_DIR / "pages" / "schema_changes.py")


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in os.environ if n.startswith("SNOWFLAKE_")] + ["ICECORE_VIEWS_SCHEMA"]:
        monkeypatch.delenv(name, raising=False)
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


def _change(day, kind, relation="DB.S.ORDERS", column="C", frm=None, to=None, node="model.p.orders"):
    return {"relation": relation, "change_kind": kind, "column": column, "from_type": frm, "to_type": to,
            "detected_at": datetime(2026, 11, day, 10), "invocation_id": "i", "node_unique_id": node,
            "emit_query_id": "q"}


CHANGES = [
    _change(3, "added", column="NEW", to="NUMBER"),
    _change(3, "removed", column="OLD", frm="TEXT", relation="DB.S.USERS", node="model.p.users"),
    _change(2, "type_changed", column="AMOUNT", frm="NUMBER(10,2)", to="FLOAT"),
    _change(2, "added", column="B", to="TEXT", relation="DB.S.USERS", node="model.p.users"),
]


def test_tiles_count_changes_tables_and_kinds():
    from lib.pages import schema_changes as sc

    tiles = {t.label: t.value for t in sc.tiles(CHANGES)}
    assert tiles == {"Changes": 4, "Tables affected": 2, "Columns added": 2, "Columns removed": 1,
                     "Type changes": 1}


def test_schema_all_empty():
    from lib.pages import schema_changes as sc

    assert [t.value for t in sc.tiles([])] == [0, 0, 0, 0, 0]
    assert sc.changes_table([]) == [] and sc.per_day([]) == []
    assert sc.latest_snapshot([]) == []


def test_changes_table_newest_first_and_opens_the_node():
    from lib.pages import schema_changes as sc

    rows = sc.changes_table(list(reversed(CHANGES)))
    assert [r["detected_at"].day for r in rows] == [3, 3, 2, 2]
    first = rows[0]
    assert first["unique_id"] == first["node_unique_id"] if "node_unique_id" in first else True
    assert {"detected_at", "relation", "change_kind", "column", "from_type", "to_type", "unique_id"} <= set(first)
    assert all(r["unique_id"] for r in rows)


def test_changes_table_keeps_markup_as_text():
    from lib.pages import schema_changes as sc

    (row,) = sc.changes_table([_change(3, "added", column="<script>x</script>", relation="a|b")])
    assert row["column"] == "<script>x</script>" and row["relation"] == "a|b"


def test_per_day_stacked_by_kind():
    from lib.pages import schema_changes as sc

    assert sc.per_day(CHANGES) == [
        {"day": "2026-11-02", "change_kind": "added", "changes": 1},
        {"day": "2026-11-02", "change_kind": "type_changed", "changes": 1},
        {"day": "2026-11-03", "change_kind": "added", "changes": 1},
        {"day": "2026-11-03", "change_kind": "removed", "changes": 1},
    ]


def test_rows_without_a_kind_or_time_do_not_break_it():
    from lib.pages import schema_changes as sc

    odd = [{"relation": "r", "detected_at": None}, {"change_kind": None, "detected_at": datetime(2026, 11, 3)}]
    assert sc.tiles(odd)[0].value == 2
    assert sc.per_day(odd) == [{"day": "2026-11-03", "change_kind": "unknown", "changes": 1}]
    assert len(sc.changes_table(odd)) == 2


def test_latest_snapshot_is_the_newest_complete_run_sorted_by_column():
    from lib.pages import schema_changes as sc

    old, new = datetime(2026, 11, 1), datetime(2026, 11, 2)
    rows = [
        {"relation": "r", "column": "B", "data_type": "TEXT", "snapshot_at": old, "is_new": False, "invocation_id": "a"},
        {"relation": "r", "column": "Z", "data_type": "TEXT", "snapshot_at": new, "is_new": True, "invocation_id": "b"},
        {"relation": "r", "column": "A", "data_type": "NUMBER", "snapshot_at": new, "is_new": False,
         "invocation_id": "b"},
    ]
    snap = sc.latest_snapshot(rows)
    assert [(r["column"], r["data_type"], r["new"]) for r in snap] == [("A", "NUMBER", False), ("Z", "TEXT", True)]


def test_column_snapshot_reads_one_relation_with_limit_and_window(monkeypatch):
    from lib import queries
    from lib.pages import schema_changes as sc

    seen = {}

    def fake(relation=None, limit=None, since=None, layer=None):
        seen.update(relation=relation, limit=limit, since=since)
        return [{"relation": relation, "column": "A", "data_type": "NUMBER", "snapshot_at": datetime(2026, 11, 2),
                 "is_new": False, "invocation_id": "b"}]

    monkeypatch.setattr(queries, "schema_columns", fake)
    rows, error = sc.column_snapshot("DB.S.ORDERS")
    assert error is None and [r["column"] for r in rows] == ["A"]
    assert seen["relation"] == "DB.S.ORDERS" and seen["limit"] == queries.MAX_LIMIT and seen["since"] is not None


def test_column_snapshot_read_error_is_returned_not_raised(monkeypatch):
    from lib import queries
    from lib.pages import schema_changes as sc

    def boom(*_, **__):
        raise RuntimeError("no session")

    monkeypatch.setattr(queries, "schema_columns", boom)
    assert sc.column_snapshot("r") == ([], "RuntimeError: no session")


def _run(monkeypatch, changes=None, error=None):
    from lib import queries

    def read(**kwargs):
        if error:
            raise RuntimeError(error)
        return list(changes or [])

    monkeypatch.setattr(queries, "schema_changes", read)
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_schema_all_empty_page(monkeypatch):
    at = _run(monkeypatch, [])
    assert at.title[0].value == "Schema changes" and at.info


def test_schema_read_error_shows_caption(monkeypatch):
    at = _run(monkeypatch, error="boom")
    assert at.info and any("boom" in c.value for c in at.caption)


def test_schema_page_renders_tiles_and_table(monkeypatch):
    now = datetime.now()
    changes = [{**c, "detected_at": now} for c in CHANGES]
    at = _run(monkeypatch, changes)
    assert {m.label: m.value for m in at.metric}["Changes"] == "4"
    assert not at.info
    assert len(at.dataframe) == 1
