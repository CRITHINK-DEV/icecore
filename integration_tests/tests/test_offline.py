"""Checks that need no Snowflake connection: the package parses and declares its contract."""
import os
from pathlib import Path

import pytest
import yaml
from dbt.cli.main import dbtRunner, dbtRunnerResult

IT_DIR = Path(__file__).resolve().parents[1]
REPO = IT_DIR.parent

PLACEHOLDER_ENV = {
    "SNOWFLAKE_ACCOUNT": "placeholder",
    "SNOWFLAKE_USER": "placeholder",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "/dev/null",
    "SNOWFLAKE_ROLE": "placeholder",
    "SNOWFLAKE_WAREHOUSE": "placeholder",
}


def run_dbt(args: list[str]) -> dbtRunnerResult:
    return dbtRunner().invoke(args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])


@pytest.fixture
def placeholder_env(monkeypatch):
    # only for this test's duration: real SNOWFLAKE_* values win, placeholders never outlive the test
    for key, value in PLACEHOLDER_ENV.items():
        if not os.environ.get(key):
            monkeypatch.setenv(key, value)


def test_dbt_parse_succeeds_without_a_connection(placeholder_env):
    assert run_dbt(["deps"]).success
    assert run_dbt(["parse"]).success


def test_package_declares_dbt_range():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))
    assert cfg["name"] == "icecore"
    assert cfg["require-dbt-version"] == [">=1.8.0", "<3.0.0"]


def test_placeholder_env_does_not_leak():
    # runs after the dbt tests above: placeholders must not reach later (sandbox) tests
    assert os.environ.get("SNOWFLAKE_ACCOUNT") != "placeholder"
