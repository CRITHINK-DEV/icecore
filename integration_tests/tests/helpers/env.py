"""Sandbox settings: every sandbox test needs these SNOWFLAKE_* variables.

Offline tests still need a parseable profile, so placeholder_env() fills in the missing
variables for the duration of one call and restores the environment afterwards: a
placeholder never reaches a later (sandbox) test, and real values always win.
"""
import os
from contextlib import contextmanager

import pytest

REQUIRED_ENV = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE"]

PLACEHOLDER_ENV = {
    "SNOWFLAKE_ACCOUNT": "placeholder",
    "SNOWFLAKE_USER": "placeholder",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "/dev/null",
    "SNOWFLAKE_ROLE": "placeholder",
    "SNOWFLAKE_WAREHOUSE": "placeholder",
}


def require_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        pytest.fail(f"sandbox tests need {', '.join(missing)}")


@contextmanager
def placeholder_env():
    """Set the placeholder SNOWFLAKE_* variables that are absent or empty, only inside the block."""
    saved = {k: os.environ.get(k) for k, v in PLACEHOLDER_ENV.items() if not os.environ.get(k)}
    for key in saved:
        os.environ[key] = PLACEHOLDER_ENV[key]
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
