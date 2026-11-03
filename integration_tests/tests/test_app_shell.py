"""The Streamlit app shell, rendered offline with streamlit.testing.v1.AppTest (no Snowflake).

Needs streamlit, which is not in the dbt venv, so it skips there. Run it from a venv with
the version pinned in streamlit/environment.yml (conftest needs dbt, hence --noconftest):

    python3.13 -m venv .venv && .venv/bin/pip install streamlit==1.50.0 pytest pyyaml
    cd integration_tests && ../.venv/bin/pytest --noconftest tests/test_app_shell.py
"""
import os
from pathlib import Path

import pytest
import yaml

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"
ENTRY = str(APP_DIR / "streamlit_app.py")
SCHEMA_INPUT_KEY = "icecore_views_schema_input"

EXPECTED_PAGES = [
    ("pages/overview.py", "Overview"),
    ("pages/incidents.py", "Incidents"),
    ("pages/tests_and_anomalies.py", "Tests & anomalies"),
    ("pages/runs.py", "Runs"),
    ("pages/schedules.py", "Schedules & freshness"),
    ("pages/schema_changes.py", "Schema changes"),
    ("pages/cost.py", "Cost"),
    ("pages/lineage.py", "Lineage"),
    ("pages/node.py", "Models & sources"),
    ("pages/settings.py", "Settings & about"),
]


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    """`streamlit run` (and Streamlit in Snowflake) puts the app directory on sys.path; AppTest does not."""
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in os.environ if n.startswith("SNOWFLAKE_")] + ["ICECORE_VIEWS_SCHEMA"]:
        monkeypatch.delenv(name, raising=False)  # a local-run environment must not make the shell connect


def run_app(page: str | None = None, at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(ENTRY, default_timeout=30)
    if page:
        at.switch_page(page)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_navigation_lists_the_ten_pages(monkeypatch):
    seen = []
    real = st.navigation

    def spy(pages, *args, **kwargs):
        seen.extend(p for group in pages.values() for p in group) if isinstance(pages, dict) else seen.extend(pages)
        return real(pages, *args, **kwargs)

    monkeypatch.setattr(st, "navigation", spy)
    run_app()
    assert [p.title for p in seen] == [title for _, title in EXPECTED_PAGES]
    assert seen[0]._default


@pytest.mark.parametrize("path,title", EXPECTED_PAGES)
def test_each_page_renders_an_empty_state(path, title):
    at = run_app(path)
    assert at.title[0].value == title
    assert at.info, f"{title} has no empty state"


def test_default_views_schema_without_a_session():
    at = run_app("pages/runs.py")
    assert "ICECORE_OBS.INVOCATIONS" in " ".join(c.value for c in at.caption)


def test_changing_the_views_schema_moves_every_page():
    at = run_app()
    at.sidebar.text_input(key=SCHEMA_INPUT_KEY).set_value("analytics.obs_v2")
    run_app(at=at)
    for path, _ in EXPECTED_PAGES:
        run_app(path, at=at)
        captions = " ".join(c.value for c in at.caption)
        assert "ANALYTICS.OBS_V2." in captions, (path, captions)
        assert "ICECORE_OBS" not in captions, (path, captions)


def test_invalid_views_schema_is_rejected_and_the_previous_one_kept():
    at = run_app()
    at.sidebar.text_input(key=SCHEMA_INPUT_KEY).set_value("obs; drop table x")
    run_app("pages/runs.py", at=at)
    assert at.sidebar.error, "no error shown for an invalid schema"
    assert "ICECORE_OBS.INVOCATIONS" in " ".join(c.value for c in at.caption)


def test_default_schema_uses_the_session_database(monkeypatch):
    from lib import settings

    monkeypatch.setattr(settings, "_current_database", lambda: '"ICECORE_SANDBOX"')
    assert settings.default_views_schema() == "ICECORE_SANDBOX.ICECORE_OBS"


def test_environment_uses_the_snowflake_channel_only():
    env = yaml.safe_load(open(APP_DIR / "environment.yml"))
    assert env["channels"] == ["snowflake"]
    deps = env["dependencies"]
    assert all(isinstance(d, str) for d in deps), "no pip section: SiS installs from the Snowflake channel only"
    assert "streamlit=1.50.0" in deps
    assert "snowflake-snowpark-python" in deps


def test_global_node_search_lists_lineage_nodes_and_opens_the_node_page(monkeypatch):
    from lib import queries

    monkeypatch.setattr(queries, "lineage_latest", lambda **_: [
        {"parent_unique_id": "source.p.raw.orders", "child_unique_id": "model.p.stg_orders"}])
    switched = []
    monkeypatch.setattr(st, "switch_page", switched.append)  # AppTest does not register st.navigation pages for it
    at = run_app()
    search = at.sidebar.selectbox(key="icecore_node_search")
    assert search.options == ["stg_orders  (model.p.stg_orders)", "orders  (source.p.raw.orders)"]
    search.select("stg_orders  (model.p.stg_orders)")
    run_app(at=at)
    assert at.session_state["icecore_console_node"] == "model.p.stg_orders"
    assert switched == ["pages/node.py"]
    # passes because switch_page is mocked above; the real one clears the query params, so the node page restores
    # ?node= from the session copy through console.sync_node_param()
    assert at.session_state["icecore_console_node"] == "model.p.stg_orders"


def test_node_page_puts_the_selected_node_back_in_the_url():
    at = run_app()
    at.session_state["icecore_console_node"] = "model.p.stg_orders"
    run_app("pages/node.py", at=at)
    assert at.query_params["node"] == ["model.p.stg_orders"]


def test_global_node_search_hides_itself_when_the_read_fails():
    at = run_app()
    assert not at.sidebar.selectbox
