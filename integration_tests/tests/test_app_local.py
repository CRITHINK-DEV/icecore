"""Running the app on a laptop: which Snowpark session the query layer builds (offline, no network).

The session factory is faked, so nothing connects. Needs streamlit (not in the dbt venv, so it skips there):

    cd integration_tests && ../.venv/bin/pytest --noconftest tests/test_app_local.py
"""
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

APP_DIR = Path(__file__).resolve().parents[2] / "streamlit"


@pytest.fixture(autouse=True)
def app_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))


@pytest.fixture
def queries():
    from lib import queries
    return queries


@pytest.fixture
def fake_snowpark(monkeypatch):
    """snowflake.snowpark with no active session and a recording Session builder."""
    created = []

    class Builder:
        def configs(self, params):
            self.params = dict(params)
            return self

        def create(self):
            created.append(self.params)
            return types.SimpleNamespace(params=self.params)

    class Session:
        builder = Builder()

    def get_active_session():
        raise RuntimeError("no active session")

    pkg = types.ModuleType("snowflake")
    pkg.__path__ = []
    snowpark = types.ModuleType("snowflake.snowpark")
    snowpark.Session = Session
    context = types.ModuleType("snowflake.snowpark.context")
    context.get_active_session = get_active_session
    monkeypatch.setitem(sys.modules, "snowflake", pkg)
    monkeypatch.setitem(sys.modules, "snowflake.snowpark", snowpark)
    monkeypatch.setitem(sys.modules, "snowflake.snowpark.context", context)
    return created, context


# --- the choice of parameters --------------------------------------------------------------------------------

def test_nothing_configured_gives_no_params(queries):
    assert queries.session_params({}) is None
    assert queries.session_params({"SNOWFLAKE_ACCOUNT": "acme"}) is None  # no user: not a login
    assert queries.session_params({"SNOWFLAKE_CONNECTION_NAME": "  "}) is None


def test_connection_name_wins_and_is_passed_whole(queries):
    env = {"SNOWFLAKE_CONNECTION_NAME": "dev", "SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_USER": "u",
           "SNOWFLAKE_PRIVATE_KEY_PATH": "/k.p8", "SNOWFLAKE_ROLE": "R"}
    assert queries.session_params(env) == {"connection_name": "dev", "role": "R"}


def test_key_pair_login(queries):
    env = {"SNOWFLAKE_ACCOUNT": "acme-x", "SNOWFLAKE_USER": "me", "SNOWFLAKE_PRIVATE_KEY_PATH": "/k.p8",
           "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "pw", "SNOWFLAKE_ROLE": "R", "SNOWFLAKE_WAREHOUSE": "W",
           "SNOWFLAKE_DATABASE": "DB"}
    assert queries.session_params(env) == {
        "account": "acme-x", "user": "me", "private_key_file": "/k.p8", "private_key_file_pwd": "pw",
        "role": "R", "warehouse": "W", "database": "DB"}


def test_without_a_key_the_browser_login_is_used(queries):
    assert queries.session_params({"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_USER": "u"}) == {
        "account": "a", "user": "u", "authenticator": "externalbrowser"}


# --- the session --------------------------------------------------------------------------------------------

def test_active_session_wins_inside_snowflake(queries, fake_snowpark):
    _, context = fake_snowpark
    context.get_active_session = lambda: "snowflake's own"
    assert queries.get_session({"SNOWFLAKE_CONNECTION_NAME": "dev"}, store={}) == "snowflake's own"
    assert fake_snowpark[0] == []


def test_no_session_and_no_config_keeps_the_empty_state(queries, fake_snowpark):
    with pytest.raises(RuntimeError, match="no active session"):
        queries.get_session({}, store={})
    assert fake_snowpark[0] == []


def test_one_local_session_per_process(queries, fake_snowpark):
    created, _ = fake_snowpark
    store: dict = {}
    env = {"SNOWFLAKE_CONNECTION_NAME": "dev"}
    first = queries.get_session(env, store)
    assert queries.get_session(env, store) is first
    assert created == [{"connection_name": "dev"}]


# --- the views schema ---------------------------------------------------------------------------------------

def test_views_schema_from_the_environment(monkeypatch):
    from lib import settings
    monkeypatch.setenv("ICECORE_VIEWS_SCHEMA", "icecore_sandbox.icecore_demo_obs")
    assert settings.default_views_schema() == "ICECORE_SANDBOX.ICECORE_DEMO_OBS"
    monkeypatch.setenv("ICECORE_VIEWS_SCHEMA", "bad name; drop")
    assert settings.default_views_schema() == "ICECORE_OBS"  # invalid value ignored, no session
