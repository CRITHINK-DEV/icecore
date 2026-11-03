"""The Elementary dbt package pinned in macros/compat/ELEMENTARY_VERSION, installed with `dbt deps` into a temporary
project, and the column definitions parsed from its own macros (macros/edr/system/system_utils/empty_table.sql),
which build Elementary's tables. The compat contract tests compare icecore's views with these, column by column.

Needs network access to the dbt package hub (as `dbt deps` does), not Snowflake.
"""
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from helpers.macros import IT_DIR

PROJECT = "icecore_elementary_package_probe"
_cache: dict = {}

# Elementary's logical column type -> the type Snowflake reports for it (INFORMATION_SCHEMA.COLUMNS.DATA_TYPE)
SNOWFLAKE_TYPE = {"string": "TEXT", "long_string": "TEXT", "bigint": "NUMBER", "int": "NUMBER", "float": "FLOAT",
                  "timestamp": "TIMESTAMP_NTZ", "boolean": "BOOLEAN"}


def pinned_version() -> str:
    for line in (IT_DIR.parent / "macros" / "compat" / "ELEMENTARY_VERSION").read_text().splitlines():
        if line.startswith("elementary_dbt_package="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("ELEMENTARY_VERSION has no elementary_dbt_package")


def package_dir() -> Path:
    """The installed package, once per session."""
    if "dir" not in _cache:
        project = Path(tempfile.mkdtemp(prefix="icecore_edr_pkg_"))
        (project / "dbt_project.yml").write_text(
            f"name: {PROJECT}\nversion: '0.0.1'\nconfig-version: 2\nprofile: icecore_sandbox\n")
        (project / "packages.yml").write_text(
            f"packages:\n  - package: elementary-data/elementary\n    version: {pinned_version()}\n")
        from dbt.cli.main import dbtRunner
        from helpers.env import placeholder_env
        with placeholder_env():
            res = dbtRunner().invoke(["deps", "--project-dir", str(project), "--profiles-dir", str(IT_DIR)])
        assert res.success, res.exception
        _cache["dir"] = project
        _cache["package"] = project / "dbt_packages" / "elementary"
    return _cache["package"]


def cleanup() -> None:
    if "dir" in _cache:
        shutil.rmtree(_cache.pop("dir"), ignore_errors=True)
        _cache.pop("package", None)


def _macro_body(text: str, name: str) -> str:
    match = re.search(r"\{%\s*macro\s+" + re.escape(name) + r"\([^)]*\)\s*%\}(.*?)\{%\s*endmacro\s*%\}", text, re.S)
    assert match, f"macro {name} not found in the Elementary package"
    return match.group(1)


def _pairs(body: str) -> list[tuple[str, str]]:
    return re.findall(r'\(\s*"(\w+)"\s*,\s*"(\w+)"\s*\)', body)


def table_columns(relation: str) -> list[tuple[str, str]]:
    """[(column, logical type)] of Elementary's table, in order, from its package macros."""
    text = (package_dir() / "macros" / "edr" / "system" / "system_utils" / "empty_table.sql").read_text()
    if relation == "data_monitoring_metrics":
        body = _macro_body(text, "default__empty_data_monitoring_metrics")
        listed = body.split("] %}", 1)[0]  # the columns list; created_at is appended after it when with_created_at
        assert re.search(r"if with_created_at.*?append\(\(\s*\"created_at\",\s*\"timestamp\"\s*\)\)", body, re.S)
        return _pairs(listed) + [("created_at", "timestamp")]
    macro = {"elementary_test_results": "empty_elementary_test_results",
             "schema_columns_snapshot": "empty_schema_columns_snapshot"}[relation]
    return _pairs(_macro_body(text, macro))


@pytest.fixture(scope="session", autouse=False)
def elementary_package():
    package_dir()
    yield
    cleanup()
