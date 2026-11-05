"""S08: the sandbox suite runs on the oldest and newest dbt Core for every release, in parallel, and never on other pull requests
or on a schedule (it spends warehouse credits). Offline checks of .github/workflows/ci.yml, dependabot.yml and README.md."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
RELEASE_IF = ("(github.event_name == 'pull_request' && github.base_ref == 'main')"
              " || (github.event_name == 'workflow_dispatch' && inputs.release)")


def triggers() -> dict:
    return CI.get("on") or CI[True]  # PyYAML reads the key `on` as True


def test_nothing_runs_on_a_schedule():
    assert "schedule" not in triggers()


def test_the_release_check_runs_on_a_pull_request_into_main_or_on_dispatch():
    job = CI["jobs"]["release-sandbox"]
    assert job["if"] == RELEASE_IF
    assert triggers()["workflow_dispatch"]["inputs"]["release"] == {
        "description": triggers()["workflow_dispatch"]["inputs"]["release"]["description"], "type": "boolean", "default": False}


def test_the_release_check_runs_the_sandbox_suite_on_floor_and_latest_in_four_parts_at_once():
    job = CI["jobs"]["release-sandbox"]
    assert job["strategy"]["matrix"] == {"requirements": ["floor", "latest"], "part": [1, 2, 3, 4]}
    assert "max-parallel" not in job["strategy"] and job["strategy"]["fail-fast"] is False
    assert job["env"]["ICECORE_TEST_SHARD"] == "${{ matrix.part }}/4"
    runs = [s.get("run", "") for s in job["steps"]]
    assert "pip install -r requirements-${{ matrix.requirements }}.txt" in runs
    assert "pytest -m sandbox -n 12 --dist loadgroup" in runs


def test_the_release_timeout_covers_one_part():
    # A part is about a quarter of the suite, 12 tests at a time; the whole suite took 2 h 12 min one test at a time.
    assert 30 <= CI["jobs"]["release-sandbox"]["timeout-minutes"] <= 120


def test_the_sandbox_jobs_never_run_on_a_pull_request_into_develop():
    single, release = CI["jobs"]["sandbox"], CI["jobs"]["release-sandbox"]
    assert single["if"] == "github.event_name == 'workflow_dispatch' && !inputs.release"
    assert "pull_request" not in single["if"]
    assert "github.base_ref == 'main'" in release["if"]  # only a release pull request
    # Every test names its objects with its own run_id, so parts and runs may overlap; a job-level concurrency group
    # would hold all parts but one back.
    assert "concurrency" not in release
    assert "pytest -m sandbox -n 12 --dist loadgroup" in [s.get("run", "") for s in single["steps"]]


def test_the_offline_checks_the_branch_rules_require_are_kept():
    assert CI["jobs"]["offline"]["strategy"]["matrix"]["requirements"] == ["floor", "latest"]
    assert CI["jobs"]["offline"]["name"] == "offline (${{ matrix.requirements }})"


def test_only_the_five_snowflake_secrets_are_used():
    secrets = {w for w in str(CI).split() if "secrets." in w}
    assert secrets and all("SNOWFLAKE" in w for w in secrets), secrets


def test_dependabot_opens_its_pull_requests_on_develop():
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
    assert [u["target-branch"] for u in config["updates"]] == ["develop"]


def test_the_readme_states_the_tested_versions():
    row = next(l for l in (REPO / "README.md").read_text().splitlines() if l.startswith("| dbt Core"))
    assert "1.8" in row and "latest" in row and "every release" in row
