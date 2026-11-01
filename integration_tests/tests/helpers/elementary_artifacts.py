"""Column definitions of Elementary's dbt_artifacts tables, parsed from the pinned package's own macros
(macros/edr/dbt_artifacts/*.sql: get_dbt_invocations_empty_table_query, get_dbt_run_results_empty_table_query),
using the package installed by helpers.elementary_package."""
import re

from helpers.elementary_package import package_dir

_MACROS = {
    "dbt_invocations": ("upload_dbt_invocation.sql", "get_dbt_invocations_empty_table_query"),
    "dbt_run_results": ("upload_run_results.sql", "get_dbt_run_results_empty_table_query"),
    "dbt_models": ("upload_dbt_models.sql", "get_dbt_models_empty_table_query"),
    "dbt_tests": ("upload_dbt_tests.sql", "get_dbt_tests_empty_table_query"),
    "dbt_sources": ("upload_dbt_sources.sql", "get_dbt_sources_empty_table_query"),
}


def artifact_columns(relation: str) -> list[tuple[str, str]]:
    """[(column, logical type)] of Elementary's table on Snowflake, in order: the base column list only. Columns that
    Elementary adds inside `{% if target.type == "bigquery" ... %}` (e.g. dbt_models' bigquery_partition_by and
    bigquery_cluster_by) are not created on Snowflake, so they are left out."""
    file, macro = _MACROS[relation]
    text = (package_dir() / "macros" / "edr" / "dbt_artifacts" / file).read_text()
    match = re.search(r"\{%\s*macro\s+" + macro + r"\([^)]*\)\s*%\}(.*?)\{%\s*endmacro\s*%\}", text, re.S)
    assert match, f"{macro} not found in the Elementary package"
    base = re.split(r"\{%-?\s*if\b", match.group(1), maxsplit=1)[0]
    return re.findall(r'\(\s*"(\w+)"\s*,\s*"(\w+)"\s*\)', base)
