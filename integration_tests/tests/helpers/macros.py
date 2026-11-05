"""The one way tests run dbt and read back what a macro returned.

- call_macro(name, **kwargs): calls `icecore.<name>(**kwargs)` through the `it_call` bridge
  (integration_tests/macros/test_support/it_call.sql) and returns its return value.
- run_macro(macro, args) / run_macro_with_logs(...): run a test wrapper macro
  (integration_tests/macros/test_support/) and return the last value it logged.

Wrappers and the bridge log one line `icecore_it_result:<json>`. Every dbt call runs inside
helpers.env.placeholder_env(): missing SNOWFLAKE_* get placeholders for that call only, so offline
calls render the profile without a connection (dbt-snowflake connects lazily) and nothing leaks into
later tests; real values always win, so sandbox calls are unaffected.
"""
import json
import os
from pathlib import Path

from dbt.cli.main import dbtRunner, dbtRunnerResult

from helpers.env import placeholder_env

IT_DIR = Path(__file__).resolve().parents[2]
RESULT_PREFIX = "icecore_it_result:"
# Set by conftest once `dbt deps` ran before the pytest-xdist workers started; workers then skip it.
DEPS_DONE = "ICECORE_IT_DEPS_DONE"

_deps_done = False


class MacroFailed(Exception):
    pass


def invoke(args: list[str], callbacks=None) -> dbtRunnerResult:
    with placeholder_env():
        return dbtRunner(callbacks=callbacks or []).invoke(
            args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]
        )


def install_deps() -> None:
    """dbt deps, once per test session (under pytest-xdist, once before the workers start)."""
    global _deps_done
    if not _deps_done and not os.environ.get(DEPS_DONE):
        assert invoke(["deps"]).success
        _deps_done = True


def target_dir() -> Path:
    """Where dbt writes manifest.json: this worker's own folder under pytest-xdist (DBT_TARGET_PATH), else target/."""
    return Path(os.environ.get("DBT_TARGET_PATH") or IT_DIR / "target")


def run_macro_with_logs(macro: str, args: dict, extra: list[str] | None = None):
    """Run `macro` through dbt run-operation; return (last value logged with RESULT_PREFIX or None,
    other `icecore:` log lines). Raise MacroFailed with dbt's error lines when the operation fails."""
    install_deps()
    seen, logs, errors = [], [], []

    def collect(event) -> None:
        msg = getattr(event.info, "msg", "") or ""
        if event.info.name == "JinjaLogInfo":
            if msg.startswith(RESULT_PREFIX):
                seen.append(json.loads(msg[len(RESULT_PREFIX):]))
            elif msg.startswith("icecore:"):
                logs.append(msg)
        elif event.info.level == "error" or event.info.name == "LogDebugStackTrace":
            # Offline, dbt's rollback after a macro error fails to connect and masks the original
            # error; the stack trace still carries it.
            errors.append(msg)

    res = invoke(["run-operation", macro, "--args", json.dumps(args)] + (extra or []), [collect])
    if not res.success:
        raise MacroFailed(f"{macro} failed: " + ("\n".join(errors) or str(res.exception)))
    return (seen[-1] if seen else None), logs


def run_macro(macro: str, args: dict, extra: list[str] | None = None):
    return run_macro_with_logs(macro, args, extra)[0]


def call_macro(name: str, **kwargs):
    """Return what `icecore.<name>(**kwargs)` returns; raise MacroFailed with dbt's error lines otherwise."""
    install_deps()
    seen, errors = [], []

    def collect(event) -> None:
        msg = getattr(event.info, "msg", "") or ""
        if event.info.name == "JinjaLogInfo" and msg.startswith(RESULT_PREFIX):
            seen.append(json.loads(msg[len(RESULT_PREFIX):]))
        elif event.info.level == "error" or event.info.name == "LogDebugStackTrace":
            errors.append(msg)

    res = invoke(["run-operation", "it_call", "--args", json.dumps({"name": name, "kwargs": kwargs})], [collect])
    if not res.success or not seen:
        raise MacroFailed("\n".join(errors) or str(res.exception))
    return seen[-1]
