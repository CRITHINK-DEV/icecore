"""S08: the sandbox suite runs on the oldest and newest dbt Core every night, a failure opens an issue, and the README
states the tested versions. Offline checks of .github/workflows/ci.yml and README.md."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())


def triggers() -> dict:
    return CI.get("on") or CI[True]  # PyYAML reads the key `on` as True


def test_the_workflow_runs_every_night():
    assert triggers()["schedule"][0]["cron"]


def test_the_existing_jobs_are_kept():
    assert {"offline", "sandbox"} <= set(CI["jobs"])
    assert CI["jobs"]["offline"]["strategy"]["matrix"]["requirements"] == ["floor", "latest"]
    assert CI["jobs"]["sandbox"]["if"] == "github.event_name == 'workflow_dispatch'"


def test_the_nightly_job_runs_the_sandbox_suite_on_floor_and_latest_one_at_a_time():
    job = CI["jobs"]["nightly-sandbox"]
    assert job["strategy"]["matrix"]["requirements"] == ["floor", "latest"]
    assert job["strategy"]["max-parallel"] == 1 and job["strategy"]["fail-fast"] is False
    assert "schedule" in job["if"]
    runs = [s.get("run", "") for s in job["steps"]]
    assert "pip install -r requirements-${{ matrix.requirements }}.txt" in runs
    assert "pytest -m sandbox" in runs


def test_a_failure_opens_an_issue_without_new_secrets():
    job = CI["jobs"]["nightly-sandbox"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    step = next(s for s in job["steps"] if "issue" in s.get("name", "").lower())
    assert step["if"] == "failure()" and "gh issue create" in step["run"]
    secrets = {w for w in str(CI).split() if "secrets." in w}
    assert all("SNOWFLAKE" in w for w in secrets), secrets  # the same five Snowflake secrets, nothing new


def test_the_readme_states_the_tested_versions():
    row = next(l for l in (REPO / "README.md").read_text().splitlines() if l.startswith("| dbt Core"))
    assert "1.8" in row and "latest" in row and "every night" in row
