"""Sandbox settings: every sandbox test needs these SNOWFLAKE_* variables."""
import os

import pytest

REQUIRED_ENV = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE"]


def require_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        pytest.fail(f"sandbox tests need {', '.join(missing)}")
