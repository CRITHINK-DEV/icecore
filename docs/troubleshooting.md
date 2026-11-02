# Troubleshooting

Start with `dbt run-operation icecore.doctor`. It never fails the run, and it names the first check that failed. This page is organized by what you see. Each entry gives the symptom, how to confirm it, and the fix.

Messages below were produced on a Snowflake sandbox with dbt Core 1.8.10, unless an entry says otherwise. Schema names are shortened.

- [Read doctor's output](#read-doctors-output)
- [`doctor` says no records, and `event_table` failed](#no-records-and-event_table-failed)
- [`doctor` says `levels` failed](#levels-too-low)
- [A warning says `icecore-error 2141`](#missing-procedure-or-grant-icecore-error-2141)
- [`doctor` passes every check, but there are no records](#no-records-and-all-checks-pass)
- [Records appear a few seconds late](#records-arrive-late)
- [`setup` fails when it creates the Python procedure](#the-anaconda-terms-are-not-accepted)
- [`dbt deps` says the packages.yml file is malformed](#the-packagesyml-file-is-malformed-after-switching-dbt-versions)
- [Records go to the wrong schema, or `obs_schema` looks like template text](#vars-do-not-render-jinja)
- [Messages show `'?'` where values should be](#messages-show--where-values-should-be)
- [The anomaly engine says Cortex is unavailable](#cortex-is-unavailable)
- [Costs are missing](#missing-costs)

## Read doctor's output

```bash
dbt run-operation icecore.doctor
```

`doctor` runs five checks and prints one line for each.

```text
icecore: doctor: check event_table: ok (<db>.TELEMETRY.EVENTS)
icecore: doctor: check levels: ok (LOG_LEVEL INFO, TRACE_LEVEL ALWAYS)
icecore: doctor: check ICECORE_EMIT: ok (test CALL: ok 1)
icecore: doctor: check ICECORE_EMIT_RUN: ok (test CALL: ok spans=0 logs=1)
icecore: doctor: check records: ok (12 icecore records in the last 24 h in <db>.ICECORE_OBS)
icecore: doctor: OK: 12 icecore records in the last 24 h in <db>.ICECORE_OBS
```

| Check | What it tests |
| --- | --- |
| `event_table` | The database has an active event table. |
| `levels` | On icecore's schema, `LOG_LEVEL` is at least `INFO` and `TRACE_LEVEL` is `ALWAYS`. |
| `ICECORE_EMIT`, `ICECORE_EMIT_RUN` | A test `CALL` of each procedure works. The test records carry `icecore.doctor.probe` and are not counted. |
| `records` | icecore records of the last 24 hours exist, counted on Snowflake's clock. The `capabilities` records of `setup` and `doctor` are not counted. |

The last line is one of three:

- `OK: <n> icecore records in the last 24 h`: every check passed and records exist.
- `no records in 24 h in <db>.<schema>; likely cause: <first failed check>`: no records. The cause is the first failed check of `event_table`, `levels` or the procedures. If all three passed, the cause is the hooks: see [No records, all checks pass](#no-records-and-all-checks-pass).
- `<n> icecore records ..., but <problems>`: records exist, but a check failed. Fix that check.

`doctor` also writes its own `icecore.capabilities` record, so you can run it again.

## No records, and `event_table` failed

**Symptom.** `doctor` prints the line below. The text is taken from `doctor.sql`, not run live: the sandbox has an event table, so this check passes there.

```text
icecore: doctor: check event_table: FAILED (no active event table; an admin must set one (icecore.set_telemetry_levels prints the statement))
```

**Confirm.** The value is empty:

```sql
SHOW PARAMETERS LIKE 'EVENT_TABLE' IN DATABASE <db>;
```

**Fix.** An `ACCOUNTADMIN` sets the account's event table. `setup` never runs this statement. It prints it:

```sql
ALTER ACCOUNT SET EVENT_TABLE = SNOWFLAKE.TELEMETRY.EVENTS;
```

On Enterprise edition or above, a database can have its own table instead:

```sql
CREATE EVENT TABLE IF NOT EXISTS <db>.<schema>.<event_table>;
ALTER DATABASE <db> SET EVENT_TABLE = <db>.<schema>.<event_table>;
```

Then run `dbt run-operation icecore.setup` again, so the views read the right table.

## Levels too low

**Symptom.** `doctor` prints `check levels: FAILED`, with the statement an admin can run. Records written while the levels are too low are lost or incomplete, and the `records` check is 0.

**Confirm.** Show the levels in effect on icecore's schema, not the database's:

```sql
SHOW PARAMETERS LIKE '%LEVEL' IN SCHEMA <db>.<obs_schema>;
```

icecore needs `LOG_LEVEL` of `INFO` or finer and `TRACE_LEVEL` of `ALWAYS`. `ON_EVENT` is not enough. `ICECORE_EMIT_RUN` writes spans with explicit times and no event, and under `ON_EVENT` only a span that carries an event lands, so spans are lost (spikes/GATE1.md, open item 7, measured with the S12 levels matrix).

**Fix.** `setup` runs the statement below when its role has the `MODIFY LOG LEVEL` and `MODIFY TRACE LEVEL` privileges on the account. Otherwise it prints it and an admin runs it:

```sql
ALTER SCHEMA <db>.<obs_schema> SET LOG_LEVEL = 'INFO', TRACE_LEVEL = 'ALWAYS';
```

Levels belong to the schema, so only the icecore procedures are traced. icecore never proposes `ALTER DATABASE ... SET TRACE_LEVEL`, which would trace every procedure and function in the database, and it never changes the account. A level set on the schema, or on a procedure, takes over from the database's. A session setting does not turn icecore on or off: with the session at `OFF`, records still landed (spike S1, `proc.levels_off`). To switch icecore off for a run, use `telemetry: false`.

## Missing procedure or grant: `icecore-error 2141`

**Symptom.** Your run succeeds, but prints warnings, and no records arrive:

```text
icecore: warning: telemetry not written ("<db>"."<schema>"."ICECORE_EMIT"): icecore-error 2141: SQL compilation error:
Unknown user-defined function <db>.<schema>.ICECORE_EMIT.
```

`doctor` shows the same text on `check ICECORE_EMIT` and `check ICECORE_EMIT_RUN`, with `FAILED`.

**Why it does not fail the run.** Every `CALL` is guarded: a failing `CALL` returns an error text and becomes a warning, one per hook. An unguarded `CALL` would have failed the run with `002141` (S1.md, `proc.missing_guarded`, `proc.missing_unguarded`).

**Confirm.** Snowflake gives the same error whether an object is missing or the role is not allowed to see it. S1 measured this for a schema the role has no USAGE on (S1.md, `proc.no_usage_guarded`). A missing grant on an existing procedure was not measured. So the error alone does not say which cause it is. Run this as the role that runs dbt, not as an admin:

```sql
SHOW PROCEDURES LIKE 'ICECORE_EMIT%' IN SCHEMA <db>.<obs_schema>;
```

- **Rows, with both procedures:** they exist, and the role can see them. The cause is elsewhere: check `obs_schema` against the schema `setup` created.
- **No rows:** either the procedures do not exist, or this role has no grant that lets it see them. Run the same statement as an admin or as the role that ran `setup`. If the admin sees rows, a grant is missing: use the fix below. If the admin also sees none, `setup` has not run in that schema, or `obs_schema` names another schema. Run `dbt run-operation icecore.setup`, and if you gave `setup` a `schema` argument, set the `obs_schema` var to the same name.

**Fix.** An admin, or the role that owns the procedures, runs:

```sql
GRANT USAGE ON DATABASE <db> TO ROLE <dbt_role>;
GRANT USAGE ON SCHEMA <db>.<obs_schema> TO ROLE <dbt_role>;
GRANT USAGE ON PROCEDURE <db>.<obs_schema>.ICECORE_EMIT(ARRAY) TO ROLE <dbt_role>;
GRANT USAGE ON PROCEDURE <db>.<obs_schema>.ICECORE_EMIT_RUN(ARRAY) TO ROLE <dbt_role>;
```

Re-running `setup` keeps grants made on the procedures. Run `doctor` again to confirm.

## No records, and all checks pass

**Symptom.** Every `doctor` check is `ok`, but the last line says:

```text
icecore: doctor: no records in 24 h in <db>.<schema>; likely cause: the telemetry hooks: check that dbt_project.yml has on-run-start: icecore.start_run() and on-run-end: icecore.end_run(results), and that a dbt command has run since (...)
```

**Confirm.** Look for the two lines in `dbt_project.yml`. Check that a dbt command has run since `setup`. Check that `telemetry` is not `false` in your vars.

**Fix.** Add the hooks:

```yaml
on-run-start: "{{ icecore.start_run() }}"
on-run-end: "{{ icecore.end_run(results) }}"
```

On dbt 1.8, `dbt source freshness` runs no project hooks unless `flags: {source_freshness_run_project_hooks: true}` is set in `dbt_project.yml`. dbt 1.12 runs them by default (S3.md, finding 1).

## Records arrive late

**Symptom.** You ran `dbt build`, queried a view at once, and found no rows, or fewer than expected. A minute later the rows are there.

**Confirm.** Query again.

```sql
select count(*) from <obs_schema>.TEST_RESULTS;
```

On the sandbox, a 6-node project showed 0 rows 2 s after `dbt build` ended and 4 rows 8 s after.

**Why.** Snowflake ingests event-table records with a delay. Spike S3 measured a p95 of 7.73 s and 6.12 s over two bursts of 10,000 records, a worst burst record at 8.73 s and a worst hook record at 20.3 s (S3.md, headline). Nothing was lost.

**Fix.** None needed. Wait. icecore leaves the newest minute out of metric history on purpose: the `history_delay_minutes` var, default `1` (see [Configuration](configuration.md)). Do not build a check that expects a record the moment a run ends.

## The Anaconda terms are not accepted

**Symptom.** `icecore.setup` fails at the step `create_emit_procedures`. `ICECORE_EMIT_RUN` is a Python procedure that uses Snowflake's third-party Python packages (`snowflake-snowpark-python`, `opentelemetry-api==1.38.0`), and Snowflake only allows those once the organization has accepted the Anaconda terms.

**Confirm.** The error comes from the `CREATE PROCEDURE` of `ICECORE_EMIT_RUN` and mentions the packages or the Anaconda terms. `ICECORE_EMIT` (SQL) is created fine. This entry could not be reproduced: the sandbox organization has accepted the terms, so the exact message is Snowflake's, not icecore's.

**Fix.** An organization administrator accepts the Anaconda terms in Snowsight. Then run `dbt run-operation icecore.setup` again. It is safe to repeat.

## The packages.yml file is malformed after switching dbt versions

**Symptom.** `dbt deps` or any dbt command fails:

```text
Runtime Error
  The packages.yml file in this project is malformed. Please double check
```

It follows a switch between dbt versions on the same project, for example from the latest dbt to dbt 1.8. The lock file `package-lock.yml` that a newer dbt wrote is not valid for dbt 1.8.

**Confirm.** Check which dbt wrote the file and which one runs now:

```bash
dbt --version
head -4 package-lock.yml
```

The error appeared on dbt 1.8.10 after `dbt deps` with dbt 1.12.6 wrote the file.

**Fix.** Remove what dbt wrote and install again:

```bash
rm -rf package-lock.yml dbt_packages target
dbt deps
```

Do this each time you switch between dbt versions on one project.

## Vars do not render Jinja

**Symptom.** `obs_schema` looks like template text, or `doctor` looks in a schema you did not name:

```text
icecore: doctor: no records in 24 h in <db>.{{ ENV_VAR('QS_OBS') }}; likely cause: ...
```

`setup` would create a schema with that text as its name.

**Confirm.** Look at the var in `dbt_project.yml`:

```yaml
vars:
  icecore:
    obs_schema: "{{ env_var('QS_OBS') }}"   # not rendered
```

Jinja is not rendered inside `vars:` keys that are nested in a mapping, as every key of the `icecore` var is. A top-level var with a string value, such as `my_var: "{{ env_var('X') }}"`, is rendered when `var()` reads it. The `icecore` keys are nested, so they are not.

**Fix.** Write a literal:

```yaml
vars:
  icecore:
    obs_schema: ICECORE_OBS
```

If you ran `setup` with the template text, drop the schema it created. Quote the name:

```sql
DROP SCHEMA <db>."{{ ENV_VAR('QS_OBS') }}";
```

To set the var per environment, pass it on the command line: `--vars '{icecore: {obs_schema: ICECORE_OBS_DEV}}'`. That replaces the whole `icecore` var, so repeat any other key you set.

## Messages show `'?'` where values should be

**Symptom.** The `message` column of `TEST_RESULTS`, or `dbt.result.message` in the event table, has `'?'` where you expect a value:

```text
... failed on column N with error: Numeric value '?' is not recognized
```

**Confirm.** This is the default. `result_message` is `redacted`: every quoted literal becomes `'?'` or `"?"`, and the text is cut at 1,000 characters.

**Why.** dbt's message can quote data from a Snowflake error, and people who read the event table may not be allowed to read your data.

**Fix, if you accept the exposure.** Set the var to `full` in `dbt_project.yml`:

```yaml
vars:
  icecore:
    result_message: full
```

On the sandbox, `full` stored `Numeric value 'jane@x.com' is not recognized`. The change affects new records only. Records already written stay redacted. See [Configuration](configuration.md#result_message).

## Cortex is unavailable

**Symptom.** You set `anomaly_engine: cortex` or `auto`, and scoring uses the native engine. Scored rows show `engine` = `native`.

**Confirm.** `CAPABILITIES.cortex_available` and `cortex_reason` say what `setup` found:

```sql
select database_name, cortex_available, cortex_reason from <obs_schema>.CAPABILITIES;
```

**What happens.** The Cortex engine is merged. It scores with a `SNOWFLAKE.ML.ANOMALY_DETECTION` model that a task trains per table, and when it cannot score, the native engine scores instead. Nothing fails: a test never fails because Cortex could not score. The native engine is pure SQL, with seasonal median and MAD bands, and needs no account feature. The reason is logged at debug level: run dbt with `--debug` to see `anomaly engine 'cortex' scored with native (<reason>)`. For `cortex_unavailable`, a line `icecore: Cortex is unavailable: <reason>` is also printed at the default level.

| Reason | Meaning | Fix |
| --- | --- | --- |
| `cortex_unavailable` | The `SNOWFLAKE.ML.ANOMALY_DETECTION` class is not visible to the role, or not available in the account or region. `cortex_reason` has the text. | Use `anomaly_engine: native`, or have an admin grant access. |
| `history_too_short` | `auto` and `cortex` need at least 14 days of history for the series. | Wait, or accept the native engine meanwhile. |
| `model_missing` | No model exists for the table yet: the refresh task has not run, or the series had too few points when it last ran. | Run `setup` to create the refresh task. It is created suspended: resume it, or set `cortex_refresh_task_resume: true` and run `setup` again. Then wait for a run. |
| `model_stale` | The model is older than `cortex_refresh_days` (default 7). | Check that the refresh task is resumed and running. |
| `cortex_error` | The scoring call failed for another reason. The detail has the error text. | Read the detail in the debug log. |

Models cannot be shared across roles, so they are trained and scored in the role that ran `setup`. The dbt role needs only USAGE on `ICECORE_SCORE`.

## Missing costs

**Symptom.** You set `cost: true`, and the `credits` metric or the cost record is missing for some nodes, or the run prints:

```text
icecore: warning: cost: attribution is not available yet for 3 of 5 nodes; a later run backfills them (QUERY_ATTRIBUTION_HISTORY lags by hours)
```

The `cost` var is off by default. With it off, nothing is read or written.

**Why.** Credits come from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY`, joined to the node by its query ids. That view lags by hours, Snowflake documents up to 6, so right after a run there is usually nothing to read. The run then writes nothing for those nodes and does not fail.

**Fix by cause.**

- **Attribution lag.** Nothing to fix. A later run backfills nodes of the last `cost_lookback_days` (default 7), at most `cost_backfill_limit` nodes per run. The backfilled record keeps the original invocation id. It needs a later dbt run with `cost` on: no run, no backfill.
- **0 credits after a day.** Snowflake writes no attribution row for many statements, such as views and plain table creation. After `cost_settle_hours` (default 24) a node whose queries still have no attribution is closed at the credits that were attributed, 0 when none, and its cost record carries `dbt.cost.settled = no_attribution`. It gets no `credits` metric. A 0 is not an error.
- **Missing privilege.** The role that runs dbt must read `ACCOUNT_USAGE`. An admin grants it:

  ```sql
  GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE <dbt_role>;
  ```

  or `GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <dbt_role>`. Without it the warning above carries the error text and every node stays unattributed.

`NODE_EXECUTIONS.credits` comes from the node's latest `cost` record, so a later backfill replaces an earlier value, and `INVOCATIONS.credits` sums them per run. Both stay null until a cost record lands. `NODE_EXECUTIONS.credits_settled` is `no_attribution` on a node closed with what was attributed, which is 0 when Snowflake wrote no attribution row (see the [views reference](reference/views.md)).
