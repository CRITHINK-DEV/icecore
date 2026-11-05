import os
import uuid
import zlib
from pathlib import Path

import pytest
import snowflake.connector

from helpers.env import require_env as _require_env
from helpers.macros import DEPS_DONE, install_deps, invoke

IT_DIR = Path(__file__).resolve().parents[1]

# pytest-xdist (`pytest -n auto`): each worker gets its own dbt target and log folders, so partial parsing and
# manifest.json are never shared, and `dbt deps` runs once before the workers start, so no worker rewrites
# dbt_packages/ while another reads it.
WORKER = os.environ.get("PYTEST_XDIST_WORKER")
if WORKER:
    os.environ["DBT_TARGET_PATH"] = str(IT_DIR / "target" / WORKER)
    os.environ["DBT_LOG_PATH"] = str(IT_DIR / "logs" / WORKER)


def pytest_configure(config):
    if not WORKER and getattr(config.option, "numprocesses", None):
        install_deps()
        os.environ[DEPS_DONE] = "1"


def pytest_collection_modifyitems(config, items):
    """ICECORE_TEST_SHARD=i/n (CI): keep the i-th of n parts of the tests, picked by a stable hash of each test id."""
    shard = os.environ.get("ICECORE_TEST_SHARD")
    if not shard:
        return
    index, total = (int(x) for x in shard.split("/"))
    keep = [item for item in items if zlib.crc32(item.nodeid.encode()) % total == index - 1]
    if len(keep) < len(items):
        config.hook.pytest_deselected(items=[item for item in items if item not in keep])
        items[:] = keep


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
    install_deps()
    return invoke


@pytest.fixture
def run_id() -> str:
    return uuid.uuid4().hex
