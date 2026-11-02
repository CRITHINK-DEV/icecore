"""Lineage page logic (streamlit/lib/dag.py), offline, pure Python."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "streamlit"))
from lib import dag  # noqa: E402

SRC = "source.shop.raw.orders"
STG = "model.shop.stg_orders"
MART = "model.shop.orders"
TEST = "test.shop.not_null_orders_id.abc"
EDGES = [{"parent_unique_id": SRC, "child_unique_id": STG}, {"parent_unique_id": STG, "child_unique_id": MART}]
EXECS = [
    {"unique_id": STG, "name": "stg_orders", "type": "model", "status": "error"},
    {"unique_id": MART, "name": "orders", "type": "model", "status": "success"},
    {"unique_id": TEST, "name": "not_null_orders_id", "type": "test", "status": "fail", "root_cause_unique_id": STG},
]
TESTS = [
    {"test_unique_id": TEST, "test_name": "not_null", "status": "fail", "executed_at": "2026-11-02 10:00:00"},
    {"test_unique_id": "test.shop.dbt_utils_recency_source_raw_orders_loaded_at.f1", "test_name": "dbt_utils.recency", "status": "fail",
     "executed_at": "2026-11-02 10:00:00"},
]


def test_nodes_drawn_with_status_overlays():
    g = dag.build_graph(EDGES, EXECS, [])
    assert set(g["nodes"]) == {SRC, STG, MART}
    assert g["nodes"][STG]["overlay"] == "failed"
    assert g["nodes"][MART]["overlay"] == "success"
    assert g["nodes"][SRC]["overlay"] == "none"
    assert g["layers"] == {SRC: 0, STG: 1, MART: 2}
    dot = dag.to_dot(g)
    assert dag.COLORS["failed"] in dot and f'"{SRC}" -> "{STG}"' in dot


def test_selected_failed_test_highlights_root_cause():
    g = dag.build_graph(EDGES, EXECS, TESTS)
    assert [t["test_unique_id"] for t in dag.failed_tests(TESTS)][0] in {TEST, TESTS[1]["test_unique_id"]}
    hl = dag.highlight(g, TEST, EXECS)
    assert hl["root_cause"] == STG
    dot = dag.to_dot(g, hl)
    assert "ROOT CAUSE" in dot and dag.highlight(g, None, EXECS)["root_cause"] is None


def test_no_root_cause_recorded():
    assert dag.root_cause_for("test.x", EXECS) is None


def test_stale_source_marked_from_freshness_test():
    g = dag.build_graph(EDGES, EXECS, TESTS)
    assert g["nodes"][SRC]["stale"] is True and "STALE" in dag.to_dot(g)
    assert not g["nodes"][STG]["stale"]


def test_fresh_source_and_non_freshness_failure_not_stale():
    ok = [dict(TESTS[1], status="pass")]
    assert not dag.build_graph(EDGES, EXECS, ok)["nodes"][SRC]["stale"]
    assert not dag.build_graph(EDGES, EXECS, [TESTS[0]])["nodes"][SRC]["stale"]


def test_latest_result_wins():
    older = dict(TESTS[1], status="pass", executed_at="2026-11-01 00:00:00")
    assert dag.stale_sources([SRC], [older, TESTS[1]]) == {SRC: "fail"}


def test_cycle_does_not_hang():
    g = dag.build_graph([{"parent_unique_id": "a", "child_unique_id": "b"},
                         {"parent_unique_id": "b", "child_unique_id": "a"}], [])
    assert set(g["layers"]) == {"a", "b"}


def test_archive_freshness_does_not_mark_orders():
    t = [{"test_unique_id": "test.shop.source_freshness_source_raw_orders_archive_loaded_at.a", "test_name": "freshness",
          "status": "fail", "executed_at": "2026-11-02"}]
    arch = "source.shop.raw.orders_archive"
    assert dag.stale_sources([SRC, arch], t) == {arch: "fail"}


def test_recency_on_model_does_not_mark_source():
    t = [{"test_unique_id": "test.shop.dbt_utils_recency_stg_raw_orders_loaded_at.a", "test_name": "recency",
          "status": "fail", "executed_at": "2026-11-02"}]
    assert dag.stale_sources([SRC], t) == {}


# --- page render (needs streamlit; skipped in the dbt venvs) -----------------------------------------

def _render(monkeypatch, edges, execs, tests):
    import pytest
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from lib import queries
    monkeypatch.syspath_prepend(str(Path(queries.__file__).resolve().parents[1]))
    monkeypatch.setattr(queries, "lineage_latest", lambda *a, **k: edges)
    monkeypatch.setattr(queries, "node_executions", lambda *a, **k: execs)
    monkeypatch.setattr(queries, "test_results", lambda *a, **k: tests)
    page = Path(queries.__file__).resolve().parents[1] / "pages" / "lineage.py"
    return AppTest.from_file(str(page), default_timeout=30).run()


def test_page_renders_graph_and_stale_warning(monkeypatch):
    edges = [dict(e, invocation_id="i1") for e in EDGES]
    at = _render(monkeypatch, edges, EXECS, TESTS)
    assert not at.exception
    assert any("raw.orders" in w.value or SRC in w.value for w in at.warning)
    assert at.selectbox


def test_page_empty_state(monkeypatch):
    at = _render(monkeypatch, [], [], [])
    assert not at.exception and at.info


def test_root_cause_read_in_the_tests_own_invocation():
    rows = [dict(EXECS[2], invocation_id="build"), {"unique_id": TEST, "invocation_id": "run", "status": "fail"}]
    assert dag.root_cause_for(TEST, rows, "build") == STG
    assert dag.root_cause_for(TEST, rows, "run") is None


def test_node_keeps_last_status_when_absent_from_latest_run():
    rows = [{"unique_id": STG, "status": "error", "started_at": "2026-11-01"},
            {"unique_id": STG, "status": "success", "started_at": "2026-11-02"},
            {"unique_id": MART, "status": "success", "started_at": "2026-11-01"}]
    got = {r["unique_id"]: r["status"] for r in dag.latest_per_node(rows)}
    assert got == {STG: "success", MART: "success"}


def test_page_without_session_shows_empty_state(monkeypatch):
    import pytest
    pytest.importorskip("streamlit")
    from lib import queries

    def boom(*a, **k):
        raise RuntimeError("no active session")
    monkeypatch.setattr(queries, "lineage_latest", boom)
    from streamlit.testing.v1 import AppTest
    page = Path(queries.__file__).resolve().parents[1] / "pages" / "lineage.py"
    monkeypatch.syspath_prepend(str(page.parents[1]))
    at = AppTest.from_file(str(page), default_timeout=30).run()
    assert not at.exception and at.info and at.caption
