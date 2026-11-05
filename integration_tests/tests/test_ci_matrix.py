"""S08: the sandbox suite runs on the oldest and newest dbt Core for every release, and never on other pull requests
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


def test_the_release_check_runs_the_sandbox_suite_on_floor_and_latest_one_at_a_time():
    job = CI["jobs"]["release-sandbox"]
    assert job["strategy"]["matrix"]["requirements"] == ["floor", "latest"]
    assert job["strategy"]["max-parallel"] == 1 and job["strategy"]["fail-fast"] is False
    runs = [s.get("run", "") for s in job["steps"]]
    assert "pip install -r requirements-${{ matrix.requirements }}.txt" in runs
    assert "pytest -m sandbox" in runs


def test_the_release_timeout_covers_the_full_suite():
    # A leg took about 2h12m on a runner (run 41308369096); GitHub's job limit is 360 minutes.
    assert 180 <= CI["jobs"]["release-sandbox"]["timeout-minutes"] <= 360


def test_the_sandbox_jobs_never_run_together_nor_on_a_pull_request_into_develop():
    single, release = CI["jobs"]["sandbox"], CI["jobs"]["release-sandbox"]
    assert single["if"] == "github.event_name == 'workflow_dispatch' && !inputs.release"
    assert "pull_request" not in single["if"]
    assert "github.base_ref == 'main'" in release["if"]  # only a release pull request
    for job in (single, release):
        assert job["concurrency"] == {"group": "icecore-sandbox", "cancel-in-progress": False}


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
