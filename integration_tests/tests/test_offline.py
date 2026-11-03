"""Checks that need no Snowflake connection: the package parses and declares its contract."""
import os

import pytest
import yaml

from helpers.macros import IT_DIR, invoke as run_dbt

REPO = IT_DIR.parent


def test_dbt_parse_succeeds_without_a_connection():
    # run_dbt fills missing SNOWFLAKE_* with placeholders for each call only (helpers.env.placeholder_env)
    assert run_dbt(["deps"]).success
    assert run_dbt(["parse"]).success


def test_package_declares_dbt_range():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))
    assert cfg["name"] == "icecore"
    assert cfg["require-dbt-version"] == [">=1.8.0", "<3.0.0"]


def test_placeholder_env_does_not_leak():
    # runs after the dbt tests above: placeholders must not reach later (sandbox) tests
    assert os.environ.get("SNOWFLAKE_ACCOUNT") != "placeholder"
