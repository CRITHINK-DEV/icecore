"""Running a real dbt command with icecore's hooks (start_run, end_run) and reading back what it wrote.

- obs_schema(sf): a context manager that creates ICECORE_OBS_<id> with both emitter procedures
  (icecore.create_emit_procedures, through the it_create_emit_procedures wrapper) and drops it on exit.
- run_with_hooks(args, run_id, schema, ...): runs a dbt command against that schema with its own target path,
  and returns (dbtRunnerResult, run_results.json as a dict).
- invocation_records(sf, schema, invocation_id, ...): the records of one invocation, read with GATE1.md's base
  filter (database, schema, executable name) plus dbt.invocation_id.
- drop_fixture_views(sf, run_id): drops the views of models/hooks named with run_id.

Time windows are opened on Snowflake's clock: the laptop runs about 10.7 s ahead (S4.md).
"""
import json
import os
import shutil
import uuid
from contextlib import contextmanager

from helpers.events import assert_no_events, wait_for_events
from helpers.macros import IT_DIR, invoke, run_macro_with_logs

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "ICECORE_SANDBOX").upper()
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SPIKES").upper()
FIXTURE_MODELS = ["hooks_a", "hooks_b", "hooks_c", "hooks_fail", "hooks_dup"]

BASE_FILTER = ("resource_attributes:\"snow.database.name\"::string = %(db)s"
               " and resource_attributes:\"snow.schema.name\"::string = %(schema)s"
               " and startswith(resource_attributes:\"snow.executable.name\"::string, 'ICECORE_EMIT')"
               " and record_attributes:\"dbt.invocation_id\"::string = %(invocation_id)s")


def icecore_vars(schema: str, **settings) -> dict:
    return {"icecore": {"obs_schema": schema, **settings}}


def snowflake_since(sf, minutes: int = 10):
    """A window start on Snowflake's clock, in UTC like the event table's TIMESTAMP."""
    return sf.cursor().execute(
        f"select dateadd(minute, -{minutes}, convert_timezone('UTC', current_timestamp())::timestamp_ntz)").fetchone()[0]


@contextmanager
def obs_schema(sf):
    """ICECORE_OBS_<id> holding ICECORE_EMIT and ICECORE_EMIT_RUN, dropped on exit."""
    schema = f"ICECORE_OBS_{uuid.uuid4().hex}".upper()
    sf.cursor().execute(f'create schema "{DATABASE}"."{schema}"')
    try:
        run_macro_with_logs("it_create_emit_procedures", {}, ["--vars", json.dumps(icecore_vars(schema))])
        yield schema
    finally:
        sf.cursor().execute(f'drop schema if exists "{DATABASE}"."{schema}"')


def run_with_hooks(args: list[str], run_id: str, schema: str, extra_vars: dict | None = None, **settings):
    """Run `dbt <args>` with the hooks writing to `schema`; return (result, run_results.json)."""
    target = IT_DIR / "target" / f"hooks-{run_id}-{uuid.uuid4().hex[:8]}"
    dbt_vars = {"run_id": run_id, **icecore_vars(schema, **settings), **(extra_vars or {})}
    try:
        res = invoke(args + ["--target-path", str(target), "--vars", json.dumps(dbt_vars)])
        run_results = json.load(open(target / "run_results.json"))
    finally:
        shutil.rmtree(target, ignore_errors=True)
    return res, run_results


def record_params(schema: str, invocation_id: str) -> dict:
    return {"db": DATABASE, "schema": schema, "invocation_id": invocation_id}


def invocation_records(sf, schema: str, invocation_id: str, min_rows: int, since, settle_s: float = 20) -> list[dict]:
    """Every icecore record of one invocation; settle_s re-reads after the first match so counts are final."""
    return wait_for_events(sf, BASE_FILTER, record_params(schema, invocation_id), min_rows=min_rows, since=since,
                           settle_s=settle_s)


def assert_no_invocation_records(sf, schema: str, invocation_id: str, since, wait_s: float = 60) -> None:
    assert_no_events(sf, BASE_FILTER, record_params(schema, invocation_id), wait_s=wait_s, since=since)


def drop_fixture_views(sf, run_id: str) -> None:
    for model in FIXTURE_MODELS:
        sf.cursor().execute(f'drop view if exists "{DATABASE}"."{SCHEMA}"."{model.upper()}_{run_id.upper()}"')


def node_results(run_results: dict) -> list[dict]:
    """run_results.json results without the hook operations (dbt 1.12 lists on-run-start/end hooks there)."""
    return [r for r in run_results["results"] if not r["unique_id"].startswith("operation.")]


def run_end_logs(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["record_type"] == "LOG" and r["record_attributes"].get("icecore.kind") == "run_end"]


def run_start_events(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["record_type"] == "SPAN_EVENT" and r["record"].get("name") == "icecore.run_start"]
