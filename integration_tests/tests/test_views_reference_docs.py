"""docs/reference/views.md lists every column of every view that icecore.create_views builds (offline).

A column added to a view without a row in its section of the reference fails here, and so does a documented column the view no longer has."""
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


VIEW_NAMES = list(views())


def documented_columns(name: str) -> set:
    """The first cell of each table row of the view's section, as lower-case identifiers."""
    section = sections()["LINEAGE" if name == "LINEAGE_LATEST" else name]
    found = set()
    for line in section.splitlines():
        cells = line.split("|")
        if len(cells) > 2 and cells[1].strip().startswith("`"):
            found.update(c.lower() for c in re.findall(r"`([A-Za-z_]+)`", cells[1]))
    return found


@pytest.mark.parametrize("name", VIEW_NAMES)
def test_every_view_column_is_in_the_reference(name):
    cols = columns(views()[name])
    assert len(cols) >= 5
    missing = [c for c in cols if c not in documented_columns(name)]
    assert not missing, f"{name}: columns not in docs/reference/views.md: {missing}"


@pytest.mark.parametrize("name", VIEW_NAMES)
def test_every_documented_column_still_exists(name):
    gone = sorted(documented_columns(name) - set(columns(views()[name])))
    assert not gone, f"{name}: documented in docs/reference/views.md but not in the view: {gone}"


def test_the_reference_covers_every_view_setup_creates():
    assert set(VIEW_NAMES) <= set(sections())
