"""Run dbt from tests, and read back the JSON a wrapper macro logged.

Wrapper macros (integration_tests/macros/test_support/) log one line
``icecore_it_result:<json>``; run_macro captures it through a dbtRunner callback.
With offline=True the call runs inside helpers.env.placeholder_env(), so a profile
can be loaded without SNOWFLAKE_* variables; no connection is opened as long as the
macro runs no query, and no placeholder outlives the call.
"""
import json
from contextlib import nullcontext
from pathlib import Path

from dbt.cli.main import dbtRunner, dbtRunnerResult

from helpers.env import placeholder_env

IT_DIR = Path(__file__).resolve().parents[2]
RESULT_PREFIX = "icecore_it_result:"


def invoke(args: list[str], callbacks=None, offline: bool = False) -> dbtRunnerResult:
    with placeholder_env() if offline else nullcontext():
        return dbtRunner(callbacks=callbacks or []).invoke(
            args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]
        )


def install_deps(offline: bool = False) -> None:
    assert invoke(["deps"], offline=offline).success


def run_macro(macro: str, args: dict, offline: bool = False):
    """Run ``macro`` through dbt run-operation and return the last value it logged with RESULT_PREFIX."""
    seen = []

    def capture(event) -> None:
        msg = getattr(event.data, "msg", "")
        if event.info.name == "JinjaLogInfo" and msg.startswith(RESULT_PREFIX):
            seen.append(json.loads(msg[len(RESULT_PREFIX):]))

    res = invoke(["run-operation", macro, "--args", json.dumps(args)], callbacks=[capture], offline=offline)
    detail = res.exception or [getattr(r, "message", r) for r in getattr(res.result, "results", [])]
    assert res.success, f"{macro} failed: {detail}"
    return seen[-1] if seen else None
