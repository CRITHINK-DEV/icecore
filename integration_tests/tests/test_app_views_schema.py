"""Where the app reads the views from (lib/settings.default_views_schema), locally and in Streamlit in Snowflake.

Needs streamlit (not in the dbt venvs, so it skips there):
    cd integration_tests && $SCRATCH/stvenv/bin/python -m pytest --noconftest tests/test_app_views_schema.py
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"


class FakeSession:
    def __init__(self, database="ANALYTICS", schema="ICECORE_DEMO_OBS", has_views=True):
        self.database, self.schema, self.has_views, self.sqls = database, schema, has_views, []

    def get_current_database(self):
        return self.database

    def get_current_schema(self):
        return self.schema

    def sql(self, text):
        self.sqls.append(text)
        return self

    def collect(self):
        return [("INVOCATIONS",)] if self.has_views else []


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    from lib import queries, settings as s
    monkeypatch.delenv("ICECORE_VIEWS_SCHEMA", raising=False)
    monkeypatch.setattr(s, "APP_CONFIG", tmp_path / "icecore_app.toml")
    s.session = FakeSession()
    monkeypatch.setattr(queries, "get_session", lambda *a, **k: s.session)
    return s


def test_the_env_var_wins_for_a_local_run(settings, monkeypatch):
    monkeypatch.setenv("ICECORE_VIEWS_SCHEMA", "sandbox.demo_obs")
    settings.APP_CONFIG.write_text('views_schema = "OTHER.SCHEMA"\n')
    assert settings.default_views_schema() == "SANDBOX.DEMO_OBS"


def test_icecore_app_toml_sets_it_in_streamlit_in_snowflake(settings):
    settings.APP_CONFIG.write_text('# where the views are\nviews_schema = "analytics.icecore_obs"  # set by the deploy\n')
    assert settings.default_views_schema() == "ANALYTICS.ICECORE_OBS"


def test_an_app_created_in_the_views_schema_needs_no_setting(settings):
    assert settings.default_views_schema() == "ANALYTICS.ICECORE_DEMO_OBS"
    assert settings.session.sqls == ["show views like 'INVOCATIONS' in schema ANALYTICS.ICECORE_DEMO_OBS"]


def test_a_schema_without_the_views_falls_back_to_icecore_obs_of_the_database(settings):
    settings.session.has_views = False
    assert settings.default_views_schema() == "ANALYTICS.ICECORE_OBS"


def test_an_invalid_value_in_the_file_or_a_quoted_schema_is_never_put_in_sql(settings):
    settings.APP_CONFIG.write_text('views_schema = "a.b; drop table x"\n')
    settings.session.schema = 'bad"schema'
    settings.session.has_views = True
    assert settings.default_views_schema() == "ANALYTICS.ICECORE_OBS"
    assert settings.session.sqls == []
