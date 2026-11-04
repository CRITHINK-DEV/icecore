"""Every public macro and every generic test argument is documented (story 8887ffc2), offline.

The source of truth is the .yml next to each macro; a generic test's is macros/tests/<name>.yml, under the name test_<name> (dbt reads it only there). The check parses every macros/**/*.yml and
macros/**/*.sql (and tests/generic) and fails for a public macro, a generic test or a test argument without a description.
docs/reference/macros.md and tests.md are generated from the same files and must be up to date.
"""
import json
import shutil

import pytest

from helpers import reference_docs as rd
from helpers.macros import IT_DIR
from test_example_shop import dbt, shop_copy

REPO = IT_DIR.parent


def test_every_public_macro_and_test_argument_has_a_description():
    assert rd.problems(REPO) == []


def test_the_check_fails_for_an_undocumented_public_macro_and_test_argument(tmp_path):
    (tmp_path / "macros" / "x").mkdir(parents=True)
    (tmp_path / "tests" / "generic").mkdir(parents=True)
    (tmp_path / "macros" / "x" / "m.sql").write_text(
        "{% macro shown(a, b=none) %}{% endmacro %}\n{% macro hidden() %}{% endmacro %}\n{% macro _internal(z) %}{% endmacro %}\n")
    (tmp_path / "macros" / "x" / "m.yml").write_text(
        "version: 2\nmacros:\n  - name: shown\n    description: Does a thing.\n    arguments:\n"
        "      - {name: a, type: string, description: First.}\n      - {name: b, type: string, description: ''}\n")
    (tmp_path / "tests" / "generic" / "t.sql").write_text("{% test t(model, limit=none) %}{% endtest %}\n")
    (tmp_path / "macros" / "t.yml").write_text(
        "version: 2\nmacros:\n  - name: test_t\n    description: A test.\n    arguments:\n"
        "      - {name: model, type: relation, description: The relation.}\n")
    assert sorted(rd.problems(tmp_path)) == [
        "generic test t: argument limit has no description",
        "public macro hidden (macros/x/m.sql) has no entry in a .yml file",
        "public macro shown: argument b has no description",
    ]


def test_the_check_ignores_internal_macros_and_catches_a_missing_description(tmp_path):
    (tmp_path / "macros").mkdir()
    (tmp_path / "macros" / "m.sql").write_text("{% macro _icecore_x(a) %}{% endmacro %}\n{% macro y() %}{% endmacro %}\n")
    (tmp_path / "macros" / "m.yml").write_text("version: 2\nmacros:\n  - name: y\n    description: ' '\n")
    assert rd.problems(tmp_path) == ["public macro y has no description"]


def test_signature_parser_keeps_defaults_with_commas_and_brackets():
    assert rd.split_args("a, b=none, c=['x', 'y'], d=\"p,q\"") == [("a", None), ("b", "none"), ("c", "['x', 'y']"), ("d", '"p,q"')]


@pytest.mark.parametrize("name, render", [("macros.md", rd.render_macros), ("tests.md", rd.render_tests)])
def test_the_reference_pages_are_generated_from_the_yml(name, render):
    page = (REPO / "docs" / "reference" / name).read_text()
    assert page == render(REPO), "run: python integration_tests/tests/helpers/reference_docs.py"


def test_the_reference_pages_name_every_public_macro_and_test():
    macros = (REPO / "docs" / "reference" / "macros.md").read_text()
    tests = (REPO / "docs" / "reference" / "tests.md").read_text()
    for key, d in rd.public(rd.definitions(REPO)).items():
        if d["kind"] == "test":
            assert f"## `icecore.{d['name']}`" in tests
        else:
            assert f"### `{d['name']}`" in macros, d["name"]
    for must in ("setup", "doctor", "start_run", "end_run", "acknowledge", "estimate_telemetry_cost", "emit"):
        assert f"### `{must}`" in macros


def test_dbt_docs_manifest_of_the_example_project_describes_every_public_macro(tmp_path):
    """What `dbt docs generate` shows for the package is the parsed manifest: parse the example project and read it."""
    project = shop_copy(tmp_path)
    assert dbt(project, ["deps"]).success
    res = dbt(project, ["parse"])
    assert res.success, res.exception
    macros = json.load(open(project / "target" / "manifest.json"))["macros"]
    mine = {m["name"]: m for m in macros.values() if m["package_name"] == "icecore"}
    public = rd.public(rd.definitions(REPO))
    missing = []
    for key, d in public.items():
        name = key if d["kind"] == "test" else d["name"]
        m = mine.get(name)
        if m is None or not m["description"].strip():
            missing.append(name)
            continue
        by_name = {a["name"]: a["description"] for a in m["arguments"]}
        missing += [f"{name}.{a}" for a, _ in d["args"] if not by_name.get(a, "").strip()]
    assert missing == []
