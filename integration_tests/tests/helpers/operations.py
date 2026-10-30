"""Run a dbt run-operation offline and read back the JSON a macro logged.

Wrapper macros in integration_tests/macros log one line ``ICECORE_RESULT <json>``;
this helper captures it through a dbtRunner callback. No Snowflake connection is
opened as long as the macro runs no query.
"""
import json
import os
from pathlib import Path

from dbt.cli.main import dbtRunner

IT_DIR = Path(__file__).resolve().parents[2]
RESULT_PREFIX = "ICECORE_RESULT "

PLACEHOLDER_ENV = {
    "SNOWFLAKE_ACCOUNT": "placeholder",
    "SNOWFLAKE_USER": "placeholder",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "/dev/null",
    "SNOWFLAKE_ROLE": "placeholder",
    "SNOWFLAKE_WAREHOUSE": "placeholder",
}


def _invoke(args: list[str], callbacks=None):
    for key, value in PLACEHOLDER_ENV.items():
        os.environ.setdefault(key, value)
    runner = dbtRunner(callbacks=callbacks or [])
    return runner.invoke(args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)])


def install_deps() -> None:
    assert _invoke(["deps"]).success


def run_operation_json(macro: str, args: dict):
    """Run ``macro`` with ``args`` and return the decoded ``ICECORE_RESULT`` payload."""
    lines: list[str] = []

    def capture(event) -> None:
        if event.info.name == "JinjaLogInfo" and event.data.msg.startswith(RESULT_PREFIX):
            lines.append(event.data.msg[len(RESULT_PREFIX):])

    res = _invoke(["run-operation", macro, "--args", json.dumps(args)], callbacks=[capture])
    assert res.success, res.exception
    assert len(lines) == 1, f"expected one {RESULT_PREFIX.strip()} line, got {lines}"
    return json.loads(lines[0])
