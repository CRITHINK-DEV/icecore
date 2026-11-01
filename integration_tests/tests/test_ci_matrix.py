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
    assert CI["jobs"]["sandbox"]["if"] == "github.event_name == 'workflow_dispatch' && !inputs.nightly"


def test_the_nightly_job_runs_the_sandbox_suite_on_floor_and_latest_one_at_a_time():
    job = CI["jobs"]["nightly-sandbox"]
    assert job["strategy"]["matrix"]["requirements"] == ["floor", "latest"]
    assert job["strategy"]["max-parallel"] == 1 and job["strategy"]["fail-fast"] is False
    assert "schedule" in job["if"]
    runs = [s.get("run", "") for s in job["steps"]]
    assert "pip install -r requirements-${{ matrix.requirements }}.txt" in runs
    assert "pytest -m sandbox" in runs


def test_the_sandbox_jobs_never_run_on_a_pull_request_and_never_together():
    nightly, single = CI["jobs"]["nightly-sandbox"], CI["jobs"]["sandbox"]
    for job in (nightly, single):
        assert "pull_request" not in job["if"] and "github.event_name ==" in job["if"]
        assert job["concurrency"] == {"group": "icecore-sandbox", "cancel-in-progress": False}
    assert "inputs.nightly" in nightly["if"] and "schedule" in nightly["if"]
    assert "workflow_dispatch" in nightly["if"]
    assert single["if"].endswith("!inputs.nightly")  # a nightly dispatch runs the matrix instead of this job
    assert triggers()["workflow_dispatch"]["inputs"]["nightly"]["type"] == "boolean"


def test_a_scheduled_run_tests_develop():
    checkout = next(s for s in CI["jobs"]["nightly-sandbox"]["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert "'develop'" in checkout["with"]["ref"] and "schedule" in checkout["with"]["ref"]


def test_a_failure_opens_an_issue_from_its_own_job_without_new_secrets():
    pytest_job, issue_job = CI["jobs"]["nightly-sandbox"], CI["jobs"]["nightly-issue"]
    assert pytest_job["permissions"] == {"contents": "read"}  # the job that runs pytest cannot write issues
    assert issue_job["permissions"] == {"issues": "write"} and issue_job["needs"] == "nightly-sandbox"
    assert "failure" in issue_job["if"]
    assert "gh issue create" in "".join(s.get("run", "") for s in issue_job["steps"])
    secrets = {w for w in str(CI).split() if "secrets." in w}
    assert all("SNOWFLAKE" in w for w in secrets), secrets  # the same five Snowflake secrets, nothing new


def test_the_readme_states_the_tested_versions():
    row = next(l for l in (REPO / "README.md").read_text().splitlines() if l.startswith("| dbt Core"))
    assert "1.8" in row and "latest" in row and "every night" in row and "develop" in row
