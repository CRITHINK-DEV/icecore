# Developer setup

How to run icecore's test suites on your machine. Every command below was run as written, from a clean clone, before it went into this guide. All paths are relative to the repository root unless a command starts with `cd`.

## What you need

- Python 3.12 or newer (CI uses 3.12; the commands were checked with 3.13). dbt 1.8 does not support the newest Python releases, so use 3.12 or 3.13 if `python3 --version` reports something newer.
- git, and the GitHub CLI (`gh`) if you open pull requests from the terminal.
- For sandbox tests only: access to the Snowflake sandbox (see [Sandbox](#sandbox)).

## The two environments

icecore supports dbt-core 1.8 and later, and the offline suite runs on both ends of that range. Each end has its own virtual environment inside `integration_tests/`, both ignored by git.

| Environment | Folder | Requirements file | dbt-core |
|---|---|---|---|
| floor | `integration_tests/.venv` | `requirements-floor.txt` | 1.8.x (`dbt-core~=1.8.0`, `dbt-snowflake~=1.8.0`; 1.8.10 at the time of writing) |
| latest | `integration_tests/.venv-latest` | `requirements-latest.txt` | the newest release (1.12.6 at the time of writing) |

```bash
cd integration_tests
python3 -m venv .venv        && .venv/bin/pip install -r requirements-floor.txt
python3 -m venv .venv-latest && .venv-latest/bin/pip install -r requirements-latest.txt
.venv/bin/dbt --version        # Core installed: 1.8.x
.venv-latest/bin/dbt --version # Core installed: the latest 1.x
```

Both files also install `snowflake-connector-python`, `pytest` and `pyyaml`. Sandbox tests run on the floor environment. The test run also creates `integration_tests/dbt_packages/` (a link to the repository as the `icecore` package), `target/` and `logs/`; all three are ignored by git.

## Running the offline suite

The offline suite (`-m "not sandbox"`) needs no Snowflake connection: it parses the package and renders macros with placeholder credentials. Run it on both environments, one after the other, each from a clean state. dbt writes `target/`, `dbt_packages/` and `package-lock.yml` in `integration_tests/`, and stale copies of them can hide a broken package install.

```bash
cd integration_tests
rm -rf target dbt_packages package-lock.yml
.venv/bin/pytest -m "not sandbox"

rm -rf target dbt_packages package-lock.yml
.venv-latest/bin/pytest -m "not sandbox"
```

The three `rm` targets matter. A `package-lock.yml` written by the latest dbt cannot be read by dbt 1.8 (`The packages.yml file in this project is malformed`), and a stale `dbt_packages/` or `target/` hides problems, so clean before every run and whenever you switch environments. Both runs must pass with no failures; the number of skipped tests can differ between the two (a version-specific test is skipped on the other). Sandbox tests are deselected, not failed, so a green offline run says nothing about Snowflake behaviour: run the sandbox tests of the code you changed as well.

## Sandbox

Sandbox tests (`-m sandbox`) run against a real Snowflake account that other contributors and agents use at the same time.

### One-time setup

An account administrator runs `integration_tests/sandbox/bootstrap.sql` once, as ACCOUNTADMIN, on a dedicated sandbox account, after replacing `<PUBLIC_KEY>` with the CI user's RSA public key. It creates:

| Object | Name |
|---|---|
| Database | `ICECORE_SANDBOX` |
| Schema for tests and spikes | `ICECORE_SANDBOX.SPIKES` (owned by the CI role) |
| Schema and event table for telemetry | `ICECORE_SANDBOX.TELEMETRY`, `ICECORE_SANDBOX.TELEMETRY.EVENTS` |
| Role | `ICECORE_CI` |
| Warehouse | `ICECORE_CI_WH` (X-Small, suspends after 60 s) |
| Service user | `ICECORE_CI_USER` (key-pair login, default role `ICECORE_CI`) |

The script is safe to re-run; re-running rotates the user's public key. Contributors never run it and never change it in a feature pull request.

### Your environment file

Keep the connection settings in a file outside the repository, for example `~/.snowflake/icecore_ci.env`, as `export NAME=value` lines, and `source` it before a sandbox run. Never commit it, never paste its values into an issue, a pull request or a log. The private key is a file; the variable holds its path. `.gitignore` already excludes `*.p8`, `*.pem`, `rsa_key*` and `.env*`.

| Variable | Required | Meaning |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | yes | account identifier |
| `SNOWFLAKE_USER` | yes | the CI service user |
| `SNOWFLAKE_PRIVATE_KEY_PATH` | yes | path to the private key file (key-pair login) |
| `SNOWFLAKE_ROLE` | yes | `ICECORE_CI` |
| `SNOWFLAKE_WAREHOUSE` | yes | `ICECORE_CI_WH` |
| `SNOWFLAKE_DATABASE` | no | defaults to `ICECORE_SANDBOX` |
| `SNOWFLAKE_SCHEMA` | no | defaults to `SPIKES` |
| `ICECORE_EVENT_TABLE` | no | event table the telemetry tests read, defaults to `ICECORE_SANDBOX.TELEMETRY.EVENTS` |

A sandbox test that starts without one of the five required variables fails at once and names the missing ones.

```bash
cd integration_tests
rm -rf target dbt_packages package-lock.yml    # also after running the latest environment: see above
source ~/.snowflake/icecore_ci.env
.venv/bin/pytest -m sandbox tests/test_metadata.py -k missing_table -v    # your story's tests, not the whole suite
```

Run only the sandbox tests for the code you changed. The full sandbox suite takes many minutes of warehouse time and competes with everyone else's runs; CI runs it on request (see [CI](#ci)).

### The `run_id` rule

The `run_id` fixture (`tests/conftest.py`) is a fresh 32-character hex string for each test. Because the sandbox is shared:

1. A test creates objects only in `SPIKES`, with the `run_id` in the name in upper case: `f"UC2_COLS_RECREATED_{run_id}".upper()`. The prefix is the story or area, then what the test does, then the `run_id`.
2. Everything a test emits carries its `run_id`, and the test filters on it (`helpers.events.wait_for_events`). Never assert on "the latest rows" of the shared event table.
3. The test drops what it created in a `finally` block, so a failing test cleans up too.
4. Never touch an object whose name does not carry your own `run_id`.

If a run was interrupted, find leftovers of your own by their prefix and drop them:

```bash
source ~/.snowflake/icecore_ci.env
.venv/bin/python - <<'EOF'
import os, snowflake.connector
c = snowflake.connector.connect(account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
    private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], role=os.environ["SNOWFLAKE_ROLE"],
    warehouse=os.environ["SNOWFLAKE_WAREHOUSE"], database="ICECORE_SANDBOX", schema="SPIKES")
for row in c.cursor().execute("show tables like 'UC2\\_%' in schema ICECORE_SANDBOX.SPIKES"):
    print(row[1])          # list first; drop with: drop table "<name>"
EOF
```

Replace `UC2` with your own prefix, and read the list before dropping anything: tables of other contributors' runs may be listed too, and those are not yours to drop.

## Elementary environment (spike S4)

Spike S4 and the Elementary-compatibility checks use the `edr` command line, which pulls a newer dbt-core than the floor and would replace it. So it gets a virtual environment of its own, `integration_tests/elementary_probe/.venv`. The Elementary release is pinned in `macros/compat/ELEMENTARY_VERSION`; `requirements-elementary.txt` (with `constraints-elementary.txt`) pins the same release and the exact dbt, sqlglot and pytest versions S4 ran with.

```bash
cd integration_tests
python3 -m venv elementary_probe/.venv
elementary_probe/.venv/bin/pip install -r requirements-elementary.txt pytest
source ~/.snowflake/icecore_ci.env
elementary_probe/.venv/bin/pytest -m sandbox tests/spikes/test_s4_edr_contract.py -v    # about 2 minutes
```

The run rewrites `spikes/results/s4.json` and `spikes/results/s4_edr_queries.sql`. Commit them only when the capture is the point of your change; otherwise `git checkout spikes/results`.

The test builds a small probe project (`integration_tests/elementary_probe/`) in its own schema, `ICECORE_SANDBOX.ELEMENTARY_PROBE_<run_id>`, which the role may create (`GRANT CREATE SCHEMA` in the bootstrap) and which the test drops at the end. Without `edr` next to the interpreter the test is skipped, so the floor and latest environments never run it. Do not change the pinned versions without a new S4 capture; `tests/test_elementary_version.py` keeps the files in step.

## CI

Workflow `.github/workflows/ci.yml`.

| Trigger | Job | What runs |
|---|---|---|
| every pull request, and every push to `main` or `develop` | `offline` (two runs: `floor` and `latest`) | `pip install -r requirements-<floor or latest>.txt`, then `pytest -m "not sandbox"` on Python 3.12 |
| every night at 02:00 UTC (GitHub runs schedules from the default branch), or manual dispatch with `nightly` ticked (`gh workflow run ci -R CRITHINK-DEV/icecore --ref <branch> -f nightly=true`) | `nightly-sandbox` (two runs, `floor` and `latest`, one at a time) | the whole `pytest -m sandbox` suite on each environment; a failure opens an issue titled "Nightly sandbox suite failed on dbt floor" (or "latest"), or comments on the one already open |
| manual dispatch (Actions tab, workflow `ci`, "Run workflow"; or `gh workflow run ci -R CRITHINK-DEV/icecore --ref <branch>`) | `sandbox` | the whole `pytest -m sandbox` suite on the floor environment, then spike S4 in its own Elementary environment |

The sandbox jobs never run on a pull request: it spends warehouse credits and needs repository secrets (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`). A pull request is ready for review when `gh pr checks <number>` shows both offline runs green; say in the pull request which sandbox tests you ran locally and paste their summary.

## Branches and pull requests

- Branch from `origin/develop`, one branch per story: `story/<short-name>`. `main` is for releases.
- Write the test first and watch it fail for the right reason.
- Pull requests target `develop`. Describe the story, what changed, each acceptance criterion with its evidence, and what is not done.
- Pull requests are merged by a person, after review. Agents never merge, never push to `develop` or `main`, and never force-push a branch that is not theirs.
- Keep Jinja (`{{` and `{%`) out of yml `description` fields: dbt renders them.
- Never commit credentials, keys or env files; never change `.github/` or the bootstrap script in a feature pull request.
