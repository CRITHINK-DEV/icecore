"""Calling a package macro through `dbt run-operation it_call` and reading back its return value."""
import json
from pathlib import Path

from dbt.cli.main import dbtRunner

from helpers.env import placeholder_env

IT_DIR = Path(__file__).resolve().parents[2]
MARK = "ICECORE_RESULT "

_deps_done = False


class MacroFailed(Exception):
    pass


def _invoke(args: list[str], callbacks=None):
    # Offline calls never open a connection (dbt-snowflake connects lazily), but the profile still renders:
    # placeholders fill missing SNOWFLAKE_* for this invocation only.
    with placeholder_env():
        return dbtRunner(callbacks=callbacks or []).invoke(
            args + ["--project-dir", str(IT_DIR), "--profiles-dir", str(IT_DIR)]
        )


def call_macro(name: str, **kwargs):
    """Return what `icecore.<name>(**kwargs)` returns; raise MacroFailed with dbt's error lines otherwise."""
    global _deps_done
    if not _deps_done:
        assert _invoke(["deps"]).success
        _deps_done = True
    results: list = []
    errors: list[str] = []

    def collect(event) -> None:
        msg = getattr(event.info, "msg", "") or ""
        if event.info.name == "JinjaLogInfo" and msg.startswith(MARK):
            results.append(json.loads(msg[len(MARK):]))
        elif event.info.level == "error" or event.info.name == "LogDebugStackTrace":
            # Offline, dbt's rollback after a macro error fails to connect and masks the original
            # error; the stack trace still carries it.
            errors.append(msg)

    res = _invoke(["run-operation", "it_call", "--args", json.dumps({"name": name, "kwargs": kwargs})], [collect])
    if not res.success or not results:
        raise MacroFailed("\n".join(errors) or str(res.exception))
    return results[-1]
