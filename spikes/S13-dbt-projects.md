# Spike S13: icecore on dbt Projects on Snowflake (`EXECUTE DBT PROJECT`)

**Questions.** Does a project with icecore's hooks deploy and run as a `DBT PROJECT` object? Do `on-run-start` and
`on-run-end` run inside `EXECUTE DBT PROJECT`, and do the `ICECORE_EMIT` and `ICECORE_EMIT_RUN` CALLs succeed from there?
What identity do the records carry (GATE1.md open item 8)? Do the views fill with the same counts as a local `dbt build`?

**Scope.** Story e888b0d2, the dbt Projects on Snowflake half only. **Fusion is deferred by the owner** (2 Nov 2026): the
owner's direction is that icecore promises a Snowflake-native approach, so dbt Projects comes first. Fusion stays open
(GATE1.md open item 5).

**Run.** 2 Nov 2026, sandbox `ICECORE_SANDBOX`, role `ICECORE_CI`, Snowflake 10.40.100, Snowflake CLI 3.27.0. The dbt inside
Snowflake was 1.9.4 (dbt-snowflake 1.9.2); the local comparison used dbt-core 1.12.5. icecore is `origin/develop` at 5c95f26.
Raw results: `spikes/results/s13.json` (every number below is a key there; sources are named in each key). Probe:
`integration_tests/macros/spikes/s13_probe.sql`.

## Headline

1. **It works with no grant.** `ICECORE_CI` deployed the project and ran it with the privileges it already had. No
   `ask_human` was needed (`run.grants_needed` is empty). The one missing piece is network access: Hub packages cannot be
   installed, so the pilot vendors its packages (see "Method").
2. **Both hooks run, and both icecore procedures work from inside the job.** `on-run-start` and `on-run-end` ran (4 hook
   lines `OK`), the `CALL` of `ICECORE_EMIT` took 0.597 s and the `CALL` of `ICECORE_EMIT_RUN` 3.068 s, and no
   `icecore-error` string appears in the log (`hooks`). The Python procedure ran, so the Anaconda terms were not a blocker
   in this account.
3. **The views fill with identical counts to a local build.** INVOCATIONS, NODE_EXECUTIONS, TEST_RESULTS and LINEAGE give
   1 / 182 / 151 / 34 rows for both the local build and the native run, with the same statuses (`view_counts`).
4. **icecore's own records are unaffected by the runtime; the native records are separate and are not read by any view.**
   icecore's 528 records of the run carry the procedures' identity, and the base filter keeps all of them. The 3,214 native
   records carry `snow.executable.type = DBT_PROJECT` and are excluded by design (`window_partition_run_1`).
5. **One thing broke: `dbt.runtime` read `core`.** `_icecore_runtime()` had no way to tell the job from a local run, so the
   INVOCATIONS view labelled native runs `core`. Fixed in this PR (see "Code change").

## Method

- **Copy.** `/Users/anouarzbaida/Documents/CRITHINK/projects/icecore-review/pilot-dataops` is a copy of the owner's
  `dataops-modernstack-blueprint` project (30 models, 4 seeds, 1 snapshot, 151 data tests). The original folder and its git
  repository were not touched. The copy has no `.git`.
- **Changes to the copy.**
  - icecore (a copy of `origin/develop` at 5c95f26) as a local package in `vendor/icecore`, with
    `on-run-start: icecore.start_run()` and `on-run-end: icecore.end_run(results)` added after the project's own hooks, and the
    var `icecore: {obs_schema: PILOT_OBS}`.
  - `generate_schema_name` prefixes zone schemas with `PILOT_`; the profile target `dev` points to `ICECORE_SANDBOX`, schema
    `PILOT_DEV`. All icecore objects are in `ICECORE_SANDBOX.PILOT_OBS`; the project object is
    `ICECORE_SANDBOX.PILOT.PILOT_DATAOPS`.
  - **Packages are local.** A `DBT PROJECT` object has no external access integration (`DESCRIBE DBT PROJECT` shows
    `external_access_integrations: []`) and Snowflake still runs `dbt deps` at compile, so Hub packages cannot install. The
    copy vendors `dbt_utils` 1.1.1, `dbt_expectations` 0.10.3 and `dbt_date` (their own `packages.yml` removed) and drops
    `dbt_project_evaluator` (disabled in the project) and `codegen` (unused). Creating an integration needs
    `ACCOUNTADMIN` and was not requested.
  - The seeds' inferred types failed the gold models' enforced contracts on a first build (`created_at`, `updated_at` as DATE,
    `cost` and `discount_amount` as NUMBER against TIMESTAMP_NTZ and REAL), so the copy pins them with `seeds: +column_types`.
    That is a pilot-project issue, not an icecore one.
  - The deployed `profiles.yml` has no secrets (`account` and `user` are placeholders); the local run used a profile outside the
    folder with the key pair of `ICECORE_CI`.
- **Deploy.** `snow dbt deploy PILOT_DATAOPS --source . --install-local-deps` in `ICECORE_SANDBOX.PILOT`, a schema
  `ICECORE_CI` created itself (it has CREATE SCHEMA on the database). 361 files copied.
- **Run.** `EXECUTE DBT PROJECT ICECORE_SANDBOX.PILOT.PILOT_DATAOPS ARGS='build'`, after `dbt run-operation icecore.setup` in
  `PILOT_OBS` and one local `dbt build --full-refresh` as the baseline. The log comes from `SYSTEM$GET_DBT_LOG(query_id,
  1000000)`; without the line limit only the last 1,000 of 9,031 lines come back.

## Results

### 1 and 2. Deploy, run, hooks

| | Native run | Local run |
|---|---|---|
| Statement | `EXECUTE DBT PROJECT ... ARGS='build'`, query `01c826c3-0000-b699-0001-19a20125e706` | `dbt build --full-refresh` |
| dbt | 1.9.4 | 1.12.5 |
| Elapsed | 90.9 s (dbt: 75.89 s) | about 20 s |
| Result | `PASS=176 WARN=0 ERROR=1 SKIP=9 TOTAL=186` | `PASS=176 WARN=0 ERROR=1 SKIP=9 TOTAL=186` |
| Invocation id | `1bcda61d-0b25-474a-9b1f-642cb0bd8bf6` | `db9c31fb-34f8-4520-84d4-1db1aacf480c` |

The one error is real and the same in both: the `dbt_expectations` regex test on `dim_customers.email` finds 7 rows. The
`EXECUTE` statement therefore ends `FAILED_WITH_ERROR`, error 210012, which is dbt's own exit code (`execute_build_1`).

- **Hooks.** `on-run-start.0` and `.1` (the project's `set_job_query_tag`, 0.59 s, and `icecore.start_run`, 0.90 s) and
  `on-run-end.0` and `.1` (`alter session unset query_tag`, 0.62 s, and `icecore.end_run`, 10.03 s) all ran. The project's own
  `alter session set query_tag` also works inside the job.
- **Procedures.** `CALL "ICECORE_SANDBOX"."PILOT_OBS"."ICECORE_EMIT"` and `ICECORE_EMIT_RUN` both returned without an
  `icecore-error`. Records landed: 184 LOG, 174 SPAN, 169 SPAN_EVENT from `ICECORE_EMIT_RUN` and 1 SPAN_EVENT from
  `ICECORE_EMIT` (`icecore_records_of_run_1`). The procedures are `EXECUTE AS OWNER` and the owner is the same role that ran
  the job, so this does not test a second role.
- **Session levels inside the job** are `LOG_LEVEL OFF` and `TRACE_LEVEL OFF` (`runtime_signals.session`), as in the Gate 1
  measurement for a session with levels off: the records still landed because the procedures' schema levels apply.
- **`run-operation` fires no hooks**, as in dbt Core.
- **Every deploy runs an auto-compile** (`auto_compile: true`; query text `execute dbt project from @...STAGE`, 37.5 to
  42.3 s). It fires the icecore hooks too: INVOCATIONS holds a `compile` invocation of 0 nodes, about 11.5 to 12.4 s, for each
  deploy, so a CI that deploys often writes those records. They carry `snow.executable.type = DBT`, not `DBT_PROJECT`.

### 3. Identity (GATE1.md open item 8)

Two sets of records exist for one run. They do not share an id: **no native record contains the dbt invocation id**
(`dbt_invocation_id_in_any_native_record: 0`), and icecore's records carry no query id of the `EXECUTE`.

| | icecore records (from the hooks) | Native records (written by Snowflake) |
|---|---|---|
| `snow.executable.type` | `PROCEDURE` | `DBT_PROJECT` |
| `snow.executable.name` | `ICECORE_EMIT(EVENTS ARRAY):VARCHAR` or `ICECORE_EMIT_RUN(SPANS ARRAY):VARCHAR` | the project object: `PILOT_DATAOPS` |
| `snow.database.name` / `snow.schema.name` | `ICECORE_SANDBOX` / `PILOT_OBS` | `ICECORE_SANDBOX` / `PILOT` (where the object lives) |
| `snow.query.id` | the `CALL` | the `EXECUTE DBT PROJECT` query |
| Scope | `icecore`, or `global` for `ICECORE_EMIT` | `dbt.runner`, `com.dbt.runner`, `snow.dbt.logger`, `snowflake.connector.*`, `snowflake.snowpark.*`, `root`, `global` |
| Rows in the run | 528 | 3,214 |

Other resource attributes of native records: `snow.owner.name`, `snow.session.role.primary.name`, `snow.warehouse.name`,
`db.user`, `snow.executable.id`, `snow.executable.runtime.version` = `3.11`.

What the native records hold (`native_records_of_run_1`):

- **Node spans**: scope `dbt.runner`, record name = the node's unique id (`model.ci_cd_project.dim_customers`,
  `test....`), or `on-run-start`, `on-run-end`, `hook_span`, `metadata.setup`; `RECORD_ATTRIBUTES` `node.database`,
  `node.schema`, `node.materialization`, `node.status`; a link `upstream.name` to the parent node's span. 214 spans, 190
  distinct names, 188 with `node.status`.
- **The command span**: scope `com.dbt.runner`, name `build`, attributes `dbt.project.name` (the object name),
  `dbt.project.version` (`live`) and `dbt.runner.args`.
- **Logs**: `snow.dbt.logger` carries dbt's own log lines with `dbt.event.code` and `dbt.event.name`.
- The `DBT` type (`DBT_PROJECT_PILOT_DATAOPS_<epoch>_STAGE`) is the deploy-time compile, not a run.

**The predicate for native records** (to be read by views of their own, unioned with icecore's; this PR defines and measures
it, and does not build the views):

```sql
RESOURCE_ATTRIBUTES:"snow.executable.type"::string = 'DBT_PROJECT'
and RESOURCE_ATTRIBUTES:"snow.database.name"::string = '<DB>'
and RESOURCE_ATTRIBUTES:"snow.schema.name"::string = '<schema of the project object>'
and RESOURCE_ATTRIBUTES:"snow.executable.name"::string = '<project object name>'
```

Over the 130 s window of the run, one `CASE` over the three groups gave 528 rows for icecore's `base_events` identity, 3,214 for
the native predicate and 53 other rows; the two predicates never overlap, because an icecore record is written by a
`PROCEDURE` and a native one by a `DBT_PROJECT` (`window_partition_run_1`).

**Does `base_events` keep what it should?** Yes. It keeps all 528 icecore records of the native run (the views counted them),
and excludes the native ones, as GATE1.md says it does. Nothing in the filter needs changing for native runs.

**Open**: joining a native run to an icecore invocation. There is no shared id. The candidates are the time window plus the
project name (`dbt.project` is `ci_cd_project`, the dbt project's name, which differs from the object name `PILOT_DATAOPS`),
or capturing the `EXECUTE` query id in the hook (not measured: `snow.query.id` of the native records is that id, and
whether a hook can read it was not tried).

### 4. Views

Counts per invocation, from the views in `ICECORE_SANDBOX.PILOT_OBS` (`view_counts`):

| View | Local `db9c31fb` | Native `1bcda61d` |
|---|---|---|
| INVOCATIONS | 1 (182 nodes: 172 success, 1 failed, 9 skipped, 0 warned) | 1 (same) |
| NODE_EXECUTIONS | 182 (30 success, 142 pass, 1 fail, 9 skipped) | 182 (same); 169 with a query id, 173 with rows affected, both the same locally |
| TEST_RESULTS | 151 (142 pass, 1 fail, 8 skipped) | 151 (same) |
| LINEAGE | 34 | 34 |

`dbt_version` reads 1.12.5 against 1.9.4, and `runtime` read `core` for both before the fix. CREDITS is null (cost off).

## Code change

`icecore._icecore_runtime()` now returns `snowflake_native` when the invocation's `project_dir` is `/tmp/dbt` and its
`profiles_dir` starts with `/tmp/dbt_effective_profiles`. Those are the values measured inside the job
(`runtime_signals.invocation_args_dict`). Both must match; either alone leaves `core`. They are an observed property of the
runtime, **not documented by Snowflake**, so they can change; the test pins the rule, and a failure of the sandbox check
below would show a change. The probe also found `HOME=/home/udf` and `USER=udf`, `SNOWFLAKE_*` and `DBT_CLOUD_*` unset, and
no `SNOWFLAKE_QUERY_ID` variable (the only name tried for a query id).

- Test: `test_runtime_is_snowflake_native_only_inside_execute_dbt_project` (4 cases, offline) in
  `integration_tests/tests/test_attributes.py`, through the wrapper `it_runtime`.
- Confirmation in the sandbox, after the fix, on the pilot: the deploy's compile (`c09a434c-e8e0-4ce4-ab65-dcd2eab33adb`) and a
  second build (`31e5cb3a-706f-4476-b2c3-0378b57c0af4`, 182 / 172 / 1 / 9) read `snowflake_native` in INVOCATIONS
  (`runtime_signals.after_fix`).
- Docs: `docs/conventions.md` and the `base_attributes` description now say `snowflake_native` is detected.

## Findings and what is not done

1. **Nothing in icecore needs a grant for dbt Projects.** The role needs CREATE SCHEMA (or ownership of a schema) to create
   the object and the privileges it already used for icecore.
2. **No Hub packages in a `DBT PROJECT`** without an external access integration: this is a prerequisite for an icecore user
   who wants `packages.yml` with Hub entries (icecore itself is installed as a local package in the copy; a Git or Hub
   install of icecore needs the same integration). The quickstart says nothing about it yet.
3. **Deploys write icecore records** (the auto-compile). A user who deploys on every commit gets one `compile` invocation per
   deploy in INVOCATIONS.
4. **Not measured:** a second role running the job (owner's rights are the same role here), a schedule (`TASK` running
   `EXECUTE DBT PROJECT`), other commands than `build` and `compile`, `source freshness`, dbt versions of the project other
   than 1.9.4, ingestion lag of these records (the first rows were readable within about a minute of the run, as elsewhere),
   `OBSERVED_TIMESTAMP` on these records, the cost of the native telemetry (3,214 rows for one 182-node build against 528 for
   icecore's), and the views over native records.
5. **Fusion: deferred by the owner.** GATE1.md open item 5 stays open for Fusion and dbt Cloud; its `EXECUTE DBT PROJECT`
   part is measured here.
