"""Sandbox settings: every sandbox test needs these SNOWFLAKE_* variables."""
import os
from contextlib import contextmanager

import pytest

REQUIRED_ENV = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE"]


def require_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        pytest.fail(f"sandbox tests need {', '.join(missing)}")


PLACEHOLDER_ENV = {
    "SNOWFLAKE_ACCOUNT": "placeholder",
    "SNOWFLAKE_USER": "placeholder",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "/dev/null",
    "SNOWFLAKE_ROLE": "placeholder",
    "SNOWFLAKE_WAREHOUSE": "placeholder",
}


@contextmanager
def placeholder_env():
    """Fill missing SNOWFLAKE_* with placeholders so the profile renders offline, for this block only.

    Real values always win, and every variable this block set is removed on exit, so placeholders never
    reach later (sandbox) tests.
    """
    added = [key for key in PLACEHOLDER_ENV if not os.environ.get(key)]
    saved = {key: os.environ.get(key) for key in added}
    try:
        for key in added:
            os.environ[key] = PLACEHOLDER_ENV[key]
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
