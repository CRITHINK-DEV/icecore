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
    ("pages/runs.py", "Runs"),
    ("pages/tests_and_anomalies.py", "Tests and anomalies"),
    ("pages/lineage.py", "Lineage"),
    ("pages/cost.py", "Cost"),
    ("pages/slas.py", "SLAs"),
    ("pages/settings.py", "Settings"),
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


def test_navigation_lists_the_six_pages(monkeypatch):
    seen = []
    real = st.navigation

    def spy(pages, *args, **kwargs):
        seen.extend(pages)
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
