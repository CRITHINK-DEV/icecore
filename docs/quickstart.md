# Quickstart

This guide takes an empty dbt project on Snowflake to its first icecore records. It takes about 15 minutes. Most of that is the one-time Snowflake setup.

icecore is pre-release and not on dbt Hub yet. Step 2 shows the entry for the first release and what to use until then.

You need:

- dbt Core `>=1.8.0,<3.0.0` with the `dbt-snowflake` adapter, and a working profile;
- a Snowflake account with an active event table (step 1).

## 1. Prepare Snowflake

Two things must be true before the first run. Both need an admin once.

**An event table.** Every Snowflake account has the event table `SNOWFLAKE.TELEMETRY.EVENTS`. Check which one is active:

```sql
SHOW PARAMETERS LIKE 'EVENT_TABLE' IN ACCOUNT;
```

If the value is empty, an `ACCOUNTADMIN` sets it:

```sql
ALTER ACCOUNT SET EVENT_TABLE = SNOWFLAKE.TELEMETRY.EVENTS;
```

On Enterprise edition or above you can give the database its own event table instead. An admin runs:

```sql
CREATE EVENT TABLE IF NOT EXISTS <db>.<schema>.<event_table>;
ALTER DATABASE <db> SET EVENT_TABLE = <db>.<schema>.<event_table>;
```

`icecore.setup` never creates an event table or changes this association. On Standard edition, icecore uses the account's active table.

**The Anaconda terms.** `ICECORE_EMIT_RUN` is a Python procedure. It uses Snowflake's third-party Python packages, so your organization must have accepted the Anaconda terms. An organization administrator accepts them in Snowsight.

**Grants.** The role that runs `setup` needs:

- USAGE on the target database;
- CREATE SCHEMA on it, or ownership of the schema;
- CREATE PROCEDURE and CREATE VIEW on that schema;
- read access to the event table the views read (the `EVENTS_VIEWER` application role when it is the account table).

The role that runs dbt needs USAGE on the database, on the icecore schema and on both procedures, and SELECT on the views. Setup, and the roles' details, are in [Configuration](configuration.md#what-setup-creates).

`setup` also checks the log and trace levels that apply to the icecore schema (its own, or inherited from the database). It needs `LOG_LEVEL` at least `INFO` and `TRACE_LEVEL` `ALWAYS`: `ON_EVENT` drops spans written without an event. If its role may change them, it runs `ALTER SCHEMA <db>.<schema> SET ...`, which needs `MODIFY LOG LEVEL` and `MODIFY TRACE LEVEL` on the account. If not, it prints that statement for an admin. It never proposes a database-wide `TRACE_LEVEL`.

## 2. Install the package

Add icecore to `packages.yml`:

```yaml
packages:
  - package: crithink-dev/icecore
    version: [">=0.1.0-alpha", "<1.0.0"]
```

Until the first release is on dbt Hub, install from a clone of the repository:

```yaml
packages:
  - local: ../icecore
```

Then install:

```bash
dbt deps
```

```text
Installing /path/to/icecore
Installed from <local @ /path/to/icecore>
```

## 3. Add the two hooks

Add these two lines to your `dbt_project.yml`:

```yaml
on-run-start: "{{ icecore.start_run() }}"
on-run-end: "{{ icecore.end_run(results) }}"
```

Both hooks write nothing until `setup` has created the procedures. Until then, each one logs a warning and your run carries on.

## 4. Run setup

`setup` creates a schema `ICECORE_OBS` in your target database, with the two procedures and the views. It creates no tables.

```bash
dbt run-operation icecore.setup --args '{runs_per_day: 24}'
```

The output, trimmed:

```text
icecore: setup in "MY_DB"."ICECORE_OBS" (9 steps)
icecore: capabilities of MY_DB: edition ENTERPRISE (organization_usage), event table MY_DB.TELEMETRY.EVENTS (set at DATABASE level), LOG_LEVEL INFO, TRACE_LEVEL ALWAYS, Cortex yes
icecore: setup step 2/9 create_schema: CREATE SCHEMA IF NOT EXISTS "MY_DB"."ICECORE_OBS": ok
icecore: setup step 3/9 create_emit_procedures: CREATE OR REPLACE ... COPY GRANTS "MY_DB"."ICECORE_OBS"."ICECORE_EMIT", "MY_DB"."ICECORE_OBS"."ICECORE_EMIT_RUN"
icecore: telemetry levels of MY_DB.ICECORE_OBS are sufficient (LOG_LEVEL INFO, TRACE_LEVEL ALWAYS): nothing changed
icecore: setup step 4/9 set_telemetry_levels: levels sufficient, ran 0, printed 1 for an admin
icecore: setup step 5/9 record_capabilities: icecore.capabilities: ok 1
icecore: telemetry cost: 12,960 rows per month (18 rows per run for 3 nodes (...) x 24 runs per day x 30 days).
icecore: telemetry cost: 0.02 to 0.85 GB, 0.0004 to 0.0180 credits per month at 0.0212 Platform Credits per GB (1500 bytes per row to the 64 KB payload cap).
icecore: setup step 7/9 create_views: CREATE OR REPLACE VIEW ... COPY GRANTS "MY_DB"."ICECORE_OBS"."METRICS", ... "TEST_RESULTS", ... "CAPABILITIES"
icecore: setup step 8/9 create_tasks: cortex_refresh: ... (suspended); silence: not created, var alerts is false
icecore: setup step 9/9 create_alerts: var alerts is false: no alert created (set vars: icecore: alerts: true to create the alerts and the silence task and resume them)
```

Read the output for three things:

- Step 4 says `levels sufficient`, or prints the `ALTER SCHEMA` statement for an admin. With sufficient levels, `printed 1 for an admin` is not a problem: it is the recommendation to cluster the event table, an `ALTER TABLE ... CLUSTER BY (TO_DATE(TIMESTAMP), RECORD_TYPE)` that an admin may run.
- The cost line is an estimate of what icecore will write to your event table each month. `runs_per_day` is how often dbt runs.
- Steps 8 and 9 create the silence task and the alerts only when the `alerts` var is `true`. By default it is `false`, so they create no alert and no silence task. With `alerts: true` they are also **resumed**, and a resumed alert or task runs on a warehouse every period: that is warehouse time on your account. See [Alerts and tasks](configuration.md#alerts-and-tasks).

Running `setup` again changes nothing. To use another schema, pass `schema` and set the `obs_schema` var to the same name: see [Configuration](configuration.md#where-icecore-writes).

## 5. Run dbt

Add a test to a model if you have none. This one is enough:

```yaml
# models/schema.yml
models:
  - name: orders
    columns:
      - name: order_id
        data_tests: [not_null, unique]
```

Then build:

```bash
dbt build
```

```text
1 of 1 START hook: shop.on-run-end.0 ........................................ [RUN]
1 of 1 OK hook: shop.on-run-end.0 ........................................... [OK in 0.00s]
Done. PASS=3 WARN=0 ERROR=0 SKIP=0 TOTAL=3
```

The `on-run-end` hook shows `OK` in 0.00 s because it renders to an empty string. The records are sent while dbt renders the hook, so a slow `CALL` shows as a pause before this line.

## 6. Check the install

```bash
dbt run-operation icecore.doctor
```

```text
icecore: doctor: check event_table: ok (MY_DB.TELEMETRY.EVENTS)
icecore: doctor: check levels: ok (LOG_LEVEL INFO, TRACE_LEVEL ALWAYS)
icecore: doctor: check ICECORE_EMIT: ok (test CALL: ok 1)
icecore: doctor: check ICECORE_EMIT_RUN: ok (test CALL: ok spans=0 logs=1)
icecore: doctor: check records: ok (12 icecore records in the last 24 h in MY_DB.ICECORE_OBS)
icecore: doctor: OK: 12 icecore records in the last 24 h in MY_DB.ICECORE_OBS
```

`doctor` never fails the run. If a check fails, the line says `FAILED` and why. If it ends with `no records in 24 h`, the likely cause is the first failed check, or else the hooks of step 3.

Records reach the event table with a delay. Spike S3 measured a p95 of about 8 seconds and a worst case of 20 seconds. In the check for this guide, the first rows were readable about a minute after `dbt build` ended.

## 7. Query a view

Read the views, not the event table. `TEST_RESULTS` has one row per test per run:

```sql
select test_name, "COLUMN", status, failures, execution_time_s
from ICECORE_OBS.TEST_RESULTS
order by test_name;
```

```text
TEST_NAME | COLUMN   | STATUS | FAILURES | EXECUTION_TIME_S
not_null  | order_id | pass   | 0        | 0.88
unique    | order_id | pass   | 0        | 0.91
```

`COLUMN` is a reserved word, so quote it.

`CAPABILITIES` shows what `setup` detected:

```sql
select database_name, edition, event_table, log_level, trace_level
from ICECORE_OBS.CAPABILITIES;
```

```text
DATABASE_NAME | EDITION    | EVENT_TABLE                       | LOG_LEVEL | TRACE_LEVEL
MY_DB         | ENTERPRISE | MY_DB.TELEMETRY.EVENTS            | INFO      | ALWAYS
```

## Turn telemetry off

To skip icecore for one run:

```bash
dbt build --vars '{icecore: {telemetry: false}}'
```

`--vars` replaces the whole `icecore` var. If you set `obs_schema` in `dbt_project.yml`, repeat it here.

## If no records appear

Run `dbt run-operation icecore.doctor` first. A failed check names the missing procedure, grant or level. See [Troubleshooting](troubleshooting.md).

## Next

- [Configuration](configuration.md): every var, argument and view, including how much of dbt's result message is stored.
- To watch a table, add `icecore.freshness` or `icecore.volume` to its `data_tests`. Both read Snowflake metadata and scan no data, and write the `METRICS` view. Their arguments are in [Configuration](configuration.md#generic-tests).
- The other monitor tests and the app are coming.
