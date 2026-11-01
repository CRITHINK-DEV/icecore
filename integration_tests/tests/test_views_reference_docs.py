"""docs/reference/views.md lists every column of every view that icecore.create_views builds (offline).

A column added to a view without a row in its section of the reference fails here."""
import re
from pathlib import Path

import pytest

from helpers.macros import run_macro

DOC = Path(__file__).resolve().parents[2] / "docs" / "reference" / "views.md"


def views() -> dict:
    return {v["name"]: v["sql"] for v in run_macro("it_views_render", {"schema": None})}


def columns(sql: str) -> list[str]:
    """Output column names: the aliases of the last top-level select list (commas inside brackets or a CASE do not split)."""
    body = sql[sql.rindex("\nselect\n") + len("\nselect\n"):]
    body = body[:body.index("\nfrom ")]
    items, depth, current = [], 0, ""
    for token in re.findall(r"\w+|\W", body):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token.lower() == "case":
            depth += 1
        elif token.lower() == "end":
            depth -= 1
        if token == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += token
    items.append(current)
    names = []
    for item in items:
        item = " ".join(item.split())
        if not item:
            continue
        alias = re.split(r"\s+as\s+", item, flags=re.I)[-1] if re.search(r"\s+as\s+", item, flags=re.I) else item.split(".")[-1]
        names.append(alias.strip().strip('"').lower())
    return names


def sections() -> dict:
    parts = re.split(r"^## ", DOC.read_text(), flags=re.M)[1:]
    return {p.splitlines()[0].strip("` ").upper(): p for p in parts}


@pytest.mark.parametrize("name", ["METRICS", "SCHEMA_CHANGES", "TEST_RESULTS", "LINEAGE", "LINEAGE_LATEST", "CAPABILITIES", "INVOCATIONS", "NODE_EXECUTIONS"])
def test_every_view_column_is_in_the_reference(name):
    section = sections()[name if name != "LINEAGE_LATEST" else "LINEAGE_LATEST"]
    text = section.lower()
    if name == "LINEAGE_LATEST":
        text += sections()["LINEAGE"].lower()
    cols = columns(views()[name])
    assert cols
    missing = [c for c in cols if f"`{c}`" not in text]
    assert not missing, f"{name}: columns not in docs/reference/views.md: {missing}"


def test_the_reference_covers_exactly_the_views_setup_creates():
    assert {n for n in views()} <= set(sections())
