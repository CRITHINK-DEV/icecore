# Gate 2: a week of scheduled runs on a real project

Decision record, for the owner's decision. Pilot from 5 to 13 Nov 2026, findings triaged on 19 and 20 Nov 2026.

Gate 2 asks whether icecore holds up on a real dbt project that runs on a schedule, natively in Snowflake, for a week:
every run recorded, monitors that score sensibly, alerts worth reading, and nothing that breaks the user's project.

## Setup

- **Project:** a copy of a real dbt project of 191 nodes (models, dbt tests, `dbt_utils` and `dbt_expectations` tests),
  with icecore vendored as a local package (dbt Projects on Snowflake has no network access by default).
- **Runtime:** `EXECUTE DBT PROJECT ... ARGS = 'build'` on dbt 1.9.4, warehouse X-Small, from a task every 6 hours.
- **Data:** a feeder task appended synthetic orders before each build: none 15 % of the time, 1 to 5 usually, a spike of
  15 to 25 another 15 %, and a new customer 10 % of the time.
- **Monitors:** `icecore.freshness`, `icecore.volume` and `icecore.schema_changes` on three gold tables, recording only
  (anomalies warn).
- **Stop:** the feeder suspended its own task after 12 Nov 23:00 UTC; the last build ran on 13 Nov 00:00 UTC.

## Results

| Check | Result |
|---|---|
| Scheduled builds that ran | 29 of 29, none missed (task history) |
| Builds recorded in `INVOCATIONS` | 29 of 29, each with its nodes (191 per build); a build took about 80 s |
| Run status | every build ended `error`: the project's own `dbt_expectations` email regex test on one model fails on the synthetic customers. Not icecore; the task kept running (`SUSPEND_TASK_AFTER_NUM_FAILURES = 0`) |
| Metrics written | `freshness_lag_seconds` 102 points, `row_count` 102 points |
| Anomalies | `row_count` 4, all on the feeder's designed spikes. `freshness_lag_seconds` 12, all false (see finding 1) |
| Incidents | 21 `test_failure` (the failing email test, one per 24 h window), 12 `anomaly` |
| Schema changes | none, as none happened |
| Telemetry failures that failed a build | none |

## Findings and what was done

1. **Freshness alerted on noise (fixed in 0.1.2, #145).** All 12 freshness anomalies were false. 4 were a table *fresher*
   than usual, which scoring treated as an anomaly as well. 8 were lags of 4 to 11 s against bands about 1 s wide: a build
   tests its tables seconds after building them, so the usual lag is a few seconds and jitter left the band. Freshness now
   flags only a lag above the band, and the band reaches at least 60 s above the usual lag.
2. **Every deploy wrote an empty `compile` invocation (fixed in 0.1.2, #145).** `snow dbt deploy` runs `dbt compile`,
   which fires the hooks on Snowflake's dbt. icecore now records nothing for `compile`; a deploy on 20 Nov wrote none.
3. **Vendoring needs `tests/` (documented in 0.1.2).** The generic tests live in `tests/generic/`; copying only `macros/`
   loses the monitors.
4. **A newer local dbt's `package-lock.yml` breaks `snow dbt deploy` (documented in 0.1.2).** Snowflake runs its own dbt
   version and cannot read it; delete it before deploying.
5. **Cost was empty because it was off.** `credits` was null on every invocation because the `cost` var was off (its
   default then). Turned on afterwards for one build, the backfill gave 500 earlier nodes their attributed cost on
   `EXECUTE DBT PROJECT` (500 is the per-run cap), so cost works natively. Follow-up: cost on by default, with a real-time
   estimate replaced by Snowflake's attribution, and a plain warning when the backfill falls behind.
6. **icecore costs more than the project it watches.** Attributed credits of the queries under the builds: icecore's
   emit calls about 0.19 and its reads about 0.16, against about 0.03 for the project's own models and tests (27 builds;
   this split covers 0.38 of the 0.84 credits attributed under those builds). The project is small, so the ratio is
   extreme, but the overhead is what a user will check first. Follow-up: measure it per statement and cut the largest part.
7. **A persistent failure opens one incident per day.** This is the documented 24 h alert window
   (`alert_window_hours`), which doubles as a daily reminder. Kept as designed.

## Open items

- Findings 5 and 6 are being built for 0.2.0.
- Not covered by this pilot: a schema change, a missed run (silence), Cortex scoring (no model was trained), the alert
  objects (alerts were off), dbt Core runs outside Snowflake on a schedule, and Standard edition accounts.
