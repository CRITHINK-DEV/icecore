"""Checks that need no Snowflake connection: the package parses and declares its contract."""
import os
from pathlib import Path

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
    for key, value in PLACEHOLDER_ENV.items():
        os.environ.setdefault(key, value)
    return dbtRunner().invoke(args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])


def test_dbt_parse_succeeds_without_a_connection():
    assert run_dbt(["deps"]).success
    assert run_dbt(["parse"]).success


def test_package_declares_dbt_range():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))
    assert cfg["name"] == "icecore"
    assert cfg["require-dbt-version"] == [">=1.8.0", "<3.0.0"]
