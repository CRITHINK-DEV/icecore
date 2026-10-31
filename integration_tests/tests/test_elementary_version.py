"""UC8: the Elementary compatibility target is pinned to one release (offline).

macros/compat/ELEMENTARY_VERSION names one dbt package version and one CLI version; spike S4's note,
its recorded results, the probe project and the pinned requirement all agree with it.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO / "macros" / "compat" / "ELEMENTARY_VERSION"
S4_NOTE = REPO / "spikes" / "S4.md"
S4_RESULTS = REPO / "spikes" / "results" / "s4.json"
REQUIREMENT = REPO / "integration_tests" / "requirements-elementary.txt"
CONSTRAINTS = REPO / "integration_tests" / "constraints-elementary.txt"
PROBE_PACKAGES = REPO / "integration_tests" / "elementary_probe" / "packages.yml"
SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+"


def pinned() -> dict:
    """KEY=VALUE lines of ELEMENTARY_VERSION, comments and blanks ignored."""
    values = {}
    for line in VERSION_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            assert key.strip() not in values, f"{key} pinned twice"
            values[key.strip()] = value.strip()
    return values


def test_version_file_names_one_package_and_one_cli_version():
    values = pinned()
    assert set(values) == {"elementary_dbt_package", "elementary_cli"}
    for key, value in values.items():
        assert re.fullmatch(SEMVER, value), f"{key}={value!r} is not one exact version"


def test_s4_note_cites_the_pinned_versions_and_the_file():
    note = S4_NOTE.read_text()
    values = pinned()
    assert "macros/compat/ELEMENTARY_VERSION" in note
    assert f"elementary-data/elementary {values['elementary_dbt_package']}" in note
    assert f"elementary-data {values['elementary_cli']}" in note


def test_s4_results_were_captured_from_the_pinned_versions():
    versions = json.loads(S4_RESULTS.read_text())["versions"]
    values = pinned()
    assert versions["elementary_dbt_package"] == values["elementary_dbt_package"]
    assert versions["elementary_cli"] == values["elementary_cli"]


def _lines(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.strip().startswith("#")]


def test_ci_requirement_pins_the_cli_under_the_constraints():
    assert _lines(REQUIREMENT) == ["-c constraints-elementary.txt", f"elementary-data[snowflake]=={pinned()['elementary_cli']}"]


def test_constraints_pin_what_s4_ran_with():
    """dbt-core, sqlglot and pytest exact, so S4's parse_fallbacks == 0 and the drift check stay stable."""
    pins = dict(line.split("==") for line in _lines(CONSTRAINTS))
    assert {"dbt-core", "dbt-snowflake", "sqlglot", "pytest"} <= set(pins)
    assert all(re.fullmatch(SEMVER, v) for v in pins.values()), pins
    versions = json.loads(S4_RESULTS.read_text())["versions"]
    assert pins["dbt-core"] == versions["dbt_core"] and pins["dbt-snowflake"] == versions["dbt_snowflake"]


def test_probe_project_pins_the_package():
    text = PROBE_PACKAGES.read_text()
    assert re.search(r"package:\s*elementary-data/elementary\s*\n\s*version:\s*" + re.escape(pinned()["elementary_dbt_package"]) + r"\s*$", text, re.M)
