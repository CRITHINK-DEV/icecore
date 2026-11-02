# shop: a small dbt project wired with icecore

Four models, two seeds, a snapshot, a source with freshness, the two icecore hooks, and the `icecore.freshness` and
`icecore.volume` tests. One variable breaks a test on purpose so you can see a failed result land. It uses only what is
merged on `develop` today; what is not merged is listed under [Coming](#coming).

```
seeds raw_customers, raw_orders  ->  source raw  ->  stg_customers, stg_orders (views)
                                                  ->  fct_orders, dim_customers (tables)   snapshot customers_snapshot
```

What icecore adds to an ordinary project is in two places:

- `dbt_project.yml`: `on-run-start: "{{ icecore.start_run() }}"` and `on-run-end: "{{ icecore.end_run(results) }}"`.
- `models/marts/_marts.yml`: `icecore.volume: {min_rows: 1}` and `icecore.freshness: {max_lag: 1d}` on both marts.

## Run it

You need Python 3.12 or 3.13, a Snowflake account where your role can create a schema and a warehouse to run on, and key-pair login. The sandbox test (see [Test it](#test-it)) runs steps 2 and 3 and the queries of step 4 against a throwaway schema; step 1 is `icecore.setup`, which the test replaces with the equivalent procedure and view creation in a schema of its own.

```bash
python3 -m venv .venv && .venv/bin/pip install dbt-core~=1.8.0 dbt-snowflake~=1.8.0   # or the latest dbt
cd examples/shop
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PRIVATE_KEY_PATH=... SNOWFLAKE_ROLE=... SNOWFLAKE_WAREHOUSE=...
export SNOWFLAKE_DATABASE=MY_DB SHOP_SCHEMA=SHOP        # where the models and seeds go (both optional)
../../.venv/bin/dbt deps --profiles-dir .
```

1. **Install icecore in the database once.** It creates the schema `ICECORE_OBS`, the two emitter procedures and the views,
   and prints what an administrator must run if your role cannot (levels, event table). It creates no table.
   ```bash
   ../../.venv/bin/dbt run-operation icecore.setup --profiles-dir .
   ```
2. **Load the raw tables, then build.** The source `raw` points at the seeds; a source has no dependency on a seed, so load
   them first, as an EL tool would.
   ```bash
   ../../.venv/bin/dbt seed --profiles-dir .
   ../../.venv/bin/dbt build --profiles-dir . --exclude resource_type:seed
   ```
   The hooks write one start record, one record per node, the query ids, the lineage edges and an end record. The two
   icecore tests write their metrics: `row_count` and `freshness_lag_seconds`, without scanning your data.
3. **Break a test on purpose.**
   ```bash
   ../../.venv/bin/dbt build --profiles-dir . --exclude resource_type:seed --vars '{shop_fail: true}'
   ```
   Two orders lose their `customer_id`, so `not_null_fct_orders_customer_id` fails and its dependents are skipped. The default
   is `shop_fail: false`.
4. **Read the records through the views.** Records take about 8 to 20 seconds to appear (spike S3). The views are the only
   interface; do not query the event table.
   ```sql
   select test_name, status, failures, executed_at from <DB>.ICECORE_OBS.TEST_RESULTS order by executed_at desc;
   select relation, metric, value, observed_at from <DB>.ICECORE_OBS.METRICS order by observed_at desc;
   select parent_unique_id, child_unique_id from <DB>.ICECORE_OBS.LINEAGE_LATEST;
   ```
   You should see the failed `not_null` test, the `row_count` and `freshness_lag_seconds` of both marts, and the edges such as
   `stg_orders -> fct_orders`.

`dbt source freshness` also works on the source `raw` (a generous threshold, because the seed rows are dated October 2026).
On dbt 1.8 it runs no project hooks unless `flags: {source_freshness_run_project_hooks: true}` is set in `dbt_project.yml`
(spike S3).

To use icecore in your own project, replace `packages.yml` with the git reference of the repository (it is not on dbt Hub yet),
copy the two hooks and add the tests to the models you care about.

## Test it

`integration_tests/tests/test_example_shop.py` parses this project on both supported dbt versions (it runs with the offline
suite) and, in the sandbox, runs it once into a schema of its own and reads the records back through the views, then
drops everything.

## Coming

Not merged yet, so not used here: failed-row samples (off by default when they arrive), cost per node, root cause on failed
tests, anomaly scoring (native and Cortex), alerts and the silence detector, and the Streamlit app. See the
[feature status](../../README.md#features).
