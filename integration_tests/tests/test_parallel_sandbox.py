"""The sandbox suite runs in parallel: pytest-xdist with --dist loadgroup on each CI runner, ICECORE_TEST_SHARD across
runners. Tests that share a module- or class-scoped fixture (one dbt build or one observability schema for the whole
file) stay in one xdist group and one part, so that setup runs once; every other test is placed on its own."""
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

IT_DIR = Path(__file__).resolve().parents[1]
REPORT = '''
import pytest

@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    for item in items:
        mark = item.get_closest_marker("xdist_group")
        print("ITEM", item.nodeid, mark.kwargs["name"] if mark else "-")
'''


def collect(tmp_path, shard: str | None = None) -> dict[str, str]:
    """{test id: its xdist group, or "-"} of the sandbox tests, for the whole suite or one part of it."""
    (tmp_path / "groups_report.py").write_text(REPORT)
    env = {k: v for k, v in os.environ.items() if k not in ("ICECORE_TEST_SHARD", "PYTEST_XDIST_WORKER")}
    env["PYTHONPATH"] = str(tmp_path)
    if shard:
        env["ICECORE_TEST_SHARD"] = shard
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-s", "-m", "sandbox",
                          "-p", "groups_report", "-p", "no:cacheprovider"],
                         cwd=IT_DIR, env=env, capture_output=True, text=True, check=True).stdout
    return dict(line[len("ITEM "):].rsplit(" ", 1) for line in out.splitlines() if line.startswith("ITEM "))  # ids may hold spaces


def test_tests_that_share_a_module_setup_form_one_group_named_after_their_file(tmp_path):
    items = collect(tmp_path)
    hooks = {nodeid: group for nodeid, group in items.items() if nodeid.startswith("tests/test_hooks.py::")}
    assert hooks and set(hooks.values()) == {"tests/test_hooks.py"}  # its tests share the `views` build


def test_a_test_with_no_shared_setup_is_placed_on_its_own(tmp_path):
    items = collect(tmp_path)
    alone = [group for nodeid, group in items.items() if nodeid.startswith("tests/spikes/test_s1_write_mechanisms.py::")]
    assert alone and set(alone) == {"-"}


def test_xdist_runs_a_group_on_one_worker():
    # The marks alone prove nothing: pytest 9.2 builds a test's id from Node._id, so pytest-xdist 3.8's loadgroup suffix
    # (written to Node._nodeid) was lost and a file's tests were spread over every worker, each building the file's setup
    # again (trial run 42051341276). test_console_strip.py shares a module-scoped fixture and needs no Snowflake.
    env = {k: v for k, v in os.environ.items() if k not in ("ICECORE_TEST_SHARD", "PYTEST_XDIST_WORKER")}
    out = subprocess.run([sys.executable, "-m", "pytest", "tests/test_console_strip.py", "-n", "4", "--dist", "loadgroup",
                          "-v", "-p", "no:cacheprovider"], cwd=IT_DIR, env=env, capture_output=True, text=True).stdout
    workers = {line.split("]")[0] + "]" for line in out.splitlines() if line.startswith("[gw") and "PASSED" in line}
    assert len(workers) == 1, out[-2000:]


def test_the_four_parts_hold_every_sandbox_test_once_and_never_split_a_group(tmp_path):
    whole = collect(tmp_path)
    parts = [collect(tmp_path, f"{i}/4") for i in range(1, 5)]
    assert sorted(n for part in parts for n in part) == sorted(whole)
    part_of = defaultdict(set)
    for i, part in enumerate(parts):
        for nodeid, group in part.items():
            if group != "-":
                part_of[group].add(i)
    assert part_of and all(len(found) == 1 for found in part_of.values())
    assert all(len(part) > len(whole) / 8 for part in parts), [len(part) for part in parts]  # no part is nearly empty


def test_test_scale_is_timed(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("ICECORE_TEST_SHARD", "PYTEST_XDIST_WORKER")}
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "sandbox and timed",
                          "-p", "no:cacheprovider"], cwd=IT_DIR, env=env, capture_output=True, text=True).stdout
    timed = {line.split("::")[0] for line in out.splitlines() if "::" in line}
    assert timed == {"tests/test_scale.py"}, out[-1500:]
