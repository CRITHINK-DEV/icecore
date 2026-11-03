"""Console foundations: status mapping and page-anatomy helpers (pure logic, plus the Streamlit-bound ones).

Needs streamlit for lib.console, so it skips on the dbt venvs. Run it from the Streamlit 1.50 venv:

    cd integration_tests && python -m pytest --noconftest tests/test_app_console.py
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


@pytest.mark.parametrize("raw,expected", [
    ("success", "ok"), ("pass", "ok"), ("PASS", "ok"), (" Success ", "ok"),
    ("error", "failed"), ("fail", "failed"), ("runtime error", "failed"), ("failed", "failed"),
    ("warn", "warning"), ("warning", "warning"),
    ("skipped", "not_run"), (None, "not_run"), ("", "not_run"), ("something new", "not_run"),
    ("late", "late"), ("stale", "late"), ("silent", "late"),
    ("ok", "ok"), ("not_run", "not_run"),
])
def test_normalise_maps_every_dbt_status(raw, expected):
    from lib import status

    assert status.normalise(raw) == expected


def test_normalise_maps_labels_back():
    from lib.status import STATUSES, label, normalise, sort_key

    assert [normalise(label(s)) for s in STATUSES] == list(STATUSES)
    assert sort_key("🔴 Failed") == 0 and sort_key("🟢 OK") == 3


def test_label_and_sort_failures_first():
    from lib.status import STATUSES, COLOURS, label, sort_key

    assert sorted(["ok", "failed", "warning", "not_run", "late"], key=sort_key) == [
        "failed", "warning", "late", "ok", "not_run"]
    assert label("failed") == "🔴 Failed" and label("not_run") == "⚪ Not run" and label("late") == "🟣 Late"
    assert set(COLOURS) == set(STATUSES)
    assert COLOURS["failed"] == "#c62828" and COLOURS["warning"] == "#b26a00" and COLOURS["ok"] == "#2e7d32"
    assert COLOURS["late"] == "#6a4fb3" and COLOURS["not_run"] == "#9e9e9e"


def test_esc_and_md_escape_markup():
    from lib.console import esc, md

    assert esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert esc(None) == ""
    out = md("*a*_b_|c")
    import re

    assert not re.search(r"(?<!\\)[*_|]", out), out
    assert md("<script>") != "<script>" and "<" not in md("<script>")


def test_md_escapes_shortcodes_and_line_starts():
    from lib.console import md

    for text in (":smile:", ":material/x:", "# h", "- a", "1. a", "> q", "a\n- b\n# c"):
        out = md(text)
        assert "\n" not in out
        assert ":" not in out.replace("\\:", "") and not out.lstrip().startswith(("#", "-", ">"))
    assert md("1. a").startswith("1\\.")


def test_theme_config_has_only_valid_keys():
    import tomllib

    from streamlit import config

    from lib.status import COLOURS

    theme = tomllib.loads((APP_DIR / ".streamlit" / "config.toml").read_text())["theme"]
    assert set(theme) <= {k.split(".", 1)[1] for k in config._config_options_template if k.startswith("theme.")
                          and k.count(".") == 1}
    assert all(not isinstance(v, dict) for v in theme.values()), "no theme.light / theme.dark sub-sections in 1.50"
    assert {"base", "backgroundColor", "textColor"}.isdisjoint(theme)
    assert (theme["redColor"], theme["orangeColor"], theme["greenColor"], theme["violetColor"],
            theme["grayColor"]) == tuple(COLOURS[k] for k in ("failed", "warning", "ok", "late", "not_run"))


def test_truncated_flag():
    from lib.console import truncation_note

    assert truncation_note([1, 2, 3], 3) == "Showing the latest 3 rows."
    assert truncation_note([1, 2], 3) is None
    assert truncation_note([1, 2, 3], None) is None
    assert truncation_note([], 0) is None


def test_truncation_note_formats_thousands():
    from lib.console import truncation_note

    assert truncation_note([0] * 50000, 50000) == "Showing the latest 50,000 rows."


def test_guarded_returns_rows_or_the_error():
    from lib.console import guarded

    assert guarded(lambda: [{"a": 1}]) == ([{"a": 1}], None)

    def boom():
        raise RuntimeError("no session")

    rows, err = guarded(boom)
    assert rows == [] and "no session" in err


def test_navigation_pages_in_spec_order():
    from lib.navigation import PAGES

    assert [(p["title"], p["group"]) for p in PAGES] == [
        ("Overview", "main"), ("Incidents", "main"), ("Tests & anomalies", "main"), ("Runs", "main"),
        ("Schedules & freshness", "main"), ("Schema changes", "main"), ("Cost", "main"),
        ("Lineage", "explore"), ("Models & sources", "explore"), ("Settings & about", "bottom")]
    assert all({"path", "title", "icon", "group"} <= set(p) for p in PAGES)


# --- widgets, checked against the pinned Streamlit (1.50) ---------------------------------------------------------

def _run(fn):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(fn, default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_kpi_row_renders_tiles_with_sparklines_and_dashes():
    def app():
        import sys

        sys.path.insert(0, str(__import__("os").environ["APP_DIR"]))
        from lib.console import Tile, kpi_row

        kpi_row([Tile("Open", 3, delta="1", help="how", series=[1, 2, 3]), Tile("Credits", None)])

    import os

    os.environ["APP_DIR"] = str(APP_DIR)
    at = _run(app)
    assert [m.label for m in at.metric] == ["Open", "Credits"]
    assert [m.value for m in at.metric] == ["3", "—"]


def test_filter_bar_defaults_and_mirrors_query_params():
    def app():
        import os
        import sys

        sys.path.insert(0, os.environ["APP_DIR"])
        import streamlit as st
        from lib.console import filter_bar

        f = filter_bar("t", layers=["bronze", "gold"])
        st.write(f"days={f.days} layer={f.layer} statuses={f.statuses}")

    import os

    os.environ["APP_DIR"] = str(APP_DIR)
    at = _run(app)
    assert "days=15 layer=None statuses=[]" in at.markdown[-1].value
    assert at.query_params["days"] == ["15"]
    at.query_params["days"] = "7"
    at.query_params["layer"] = "gold"
    at.query_params["status"] = "failed,bogus"
    at.run()
    assert "days=7 layer=gold statuses=['failed']" in at.markdown[-1].value


def test_filter_bar_hides_layer_when_not_recorded():
    def app():
        import os
        import sys

        sys.path.insert(0, os.environ["APP_DIR"])
        from lib.console import filter_bar

        filter_bar("t", layers=None)

    import os

    os.environ["APP_DIR"] = str(APP_DIR)
    at = _run(app)
    assert not [r for r in at.radio if r.label == "Layer"] and [r for r in at.radio if r.label == "Window"]


def test_table_sorts_failures_first_and_notes_truncation():
    def app():
        import os
        import sys

        sys.path.insert(0, os.environ["APP_DIR"])
        from lib.console import table

        rows = [{"unique_id": "a", "status": "success"}, {"unique_id": "b", "status": "error"}]
        table(rows, {}, "t", limit=2)

    import os

    os.environ["APP_DIR"] = str(APP_DIR)
    at = _run(app)
    df = at.dataframe[0].value
    assert list(df["status"]) == ["🔴 Failed", "🟢 OK"]
    assert any("Showing the latest 2 rows." in c.value for c in at.caption)


def test_table_sorts_labelled_statuses():
    def app():
        import os
        import sys

        sys.path.insert(0, os.environ["APP_DIR"])
        from lib.console import table

        table([{"unique_id": "n", "status": "🟢 OK"}, {"unique_id": "f", "status": "🔴 Failed"}], {}, "t1")

    import os

    os.environ["APP_DIR"] = str(APP_DIR)
    assert list(_run(app).dataframe[0].value["unique_id"]) == ["f", "n"]
