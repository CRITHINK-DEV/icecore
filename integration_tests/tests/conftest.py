import os
import uuid
from pathlib import Path

import pytest
import snowflake.connector
from dbt.cli.main import dbtRunner, dbtRunnerResult

from helpers.env import require_env as _require_env

IT_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def sf():
    _require_env()
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
        role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES"),
        client_session_keep_alive=True,
    )
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def dbt():
    _require_env()

    def invoke(args: list[str]) -> dbtRunnerResult:
        return dbtRunner().invoke(args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])

    assert invoke(["deps"]).success
    return invoke


@pytest.fixture
def run_id() -> str:
    return uuid.uuid4().hex
