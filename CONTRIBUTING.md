# Contributing to icecore

Issues and pull requests are welcome. This page gives the rules a pull request must meet. The setup is in [docs/development.md](docs/development.md), which is the one place for it: how to build the two dbt environments, run the tests and use the sandbox. This page does not repeat it.

icecore is pre-release. Read the [design](docs/design/scope-and-architecture.md) before you propose a new module, and the [roadmap](docs/roadmap.md) to see what is planned.

## Ask first

- A question: open an issue with the question template.
- A bug: open an issue with the bug template. Paste the output of `dbt run-operation icecore.doctor`.
- A feature: open an issue with the feature template and wait for a reply before you write code. A change that does not fit the design is easier to discuss before it exists.

## Run the offline suite

You need Python 3.12 and a clone of the repository. You do not need Snowflake.

```bash
cd integration_tests
python3 -m venv .venv && .venv/bin/pip install -r requirements-floor.txt
rm -rf target dbt_packages package-lock.yml
.venv/bin/pytest -m "not sandbox"
```

This parses the package and renders its macros with placeholder credentials. [docs/development.md](docs/development.md#running-the-offline-suite) explains the second environment (latest dbt) and why you clean `target/`, `dbt_packages/` and `package-lock.yml` before every run.

## Rules for a pull request

1. **Tests first.** Write the test, run it, and watch it fail for the right reason. Then write the code.
2. **Both dbt versions.** The offline suite must pass on the floor environment (dbt-core 1.8) and on the latest. CI runs both on every pull request (`offline (floor)` and `offline (latest)`, about 7 minutes). To run them locally, start each from a clean `target/`, `dbt_packages/` and `package-lock.yml`; `pytest -m "not sandbox" -n auto` uses one worker per core.
3. **The SQL safety rule.** Package macros never `INSERT`, `MERGE`, `CREATE TABLE` or `CREATE STAGE`. icecore owns no table and no stage. Records reach the event table only by `CALL`ing the two procedures that `icecore.setup` creates, `ICECORE_EMIT` and `ICECORE_EMIT_RUN`. Never emit from an anonymous block. Never change the session's `LOG_LEVEL` or `TRACE_LEVEL`. Never `ALTER ACCOUNT`: `setup` prints such a statement for an admin and never runs it. Quote identifiers and escape strings: a value never goes into SQL text unquoted, and names pass through the package's validated identifier helper. Test code, not package macros, may create objects: test probes under `integration_tests/macros/spikes/` and the sandbox tests, and only in the `SPIKES` schema of the sandbox.
4. **No secrets.** Never commit or paste a password, a private key, an account identifier, a webhook URL or an environment file. Use placeholders such as `<account>`. This applies to issues, pull requests, logs and test output too.
5. **Docs with the code.** A macro has a `.yml` description with its arguments. A user-facing change updates the page in `docs/` that covers it. Keep Jinja (`{{` and `{%`) out of yml `description` fields: dbt renders them.
6. **Do not touch** `.github/workflows/`, credentials or the sandbox bootstrap script in a feature pull request.

## Branches and review

- Branch from `develop`: `story/<short-name>`. `main` is for releases.
- Open the pull request against `develop`. Say what changed, how you checked it, and what is not done.
- A pull request is ready for review when both local offline runs are green and their results are in the description.
- A person reviews and merges every pull request. Agents that work on this repository never merge, never push to `develop` or `main`, and never force-push a branch that is not theirs.

## The sandbox suite

Some tests run against a real Snowflake account (`-m sandbox`). You can skip them: a maintainer runs the sandbox suite for a pull request that needs it.

If you have access to a sandbox, [docs/development.md](docs/development.md#sandbox) says how to set it up, what the environment file holds, and the `run_id` rule: a test creates objects only in the `SPIKES` schema with its `run_id` in the name, tags everything it writes with that `run_id`, and drops its objects in a `finally` block. Run only the sandbox tests for the code you changed. The suite shares one account and one event table with every other contributor.

If you have no sandbox, say so in the pull request. A maintainer runs the sandbox tests for you.

## Licence

By contributing you agree that your contribution is licensed under the [Apache-2.0 licence](LICENSE) of the project.
