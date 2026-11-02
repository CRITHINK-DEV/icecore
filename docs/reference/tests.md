# Generic test reference

<!-- Generated from macros/tests/*.yml by integration_tests/tests/helpers/reference_docs.py. Edit the .yml, then regenerate. -->

The generic tests of icecore. Add them under `data_tests:` of a model, or of a column for the tests that take a column. `model` and `column_name` are filled in by dbt: you never write them. Every test accepts dbt's own `config:`, such as `severity: warn`. How the tests fit together is on the [configuration page](../configuration.md#generic-tests).

## `icecore.all_columns_anomaly`

Applies the column metrics to every column of the relation in one test, so one line of YAML covers a model. The columns are read with SHOW COLUMNS and `exclude_columns` are skipped. One change window is opened (CHANGES, or `timestamp_column` when change tracking does not cover it) and each column's metrics are computed over the changed rows only (one query per column), written through `icecore.emit_metric` one per metric with the column, and checked against the static `bounds`, which apply to every column that has that metric. The default metrics are `window_rows` and `null_rate`; avg, min and max are skipped, with a logged warning, on columns that are not numeric. Until the anomaly engines are wired in (UC3) the bounds are the only verdict, and the scoring hook is marked in the test body and not called. The previous observation is the newest column metric record of the relation, read through `icecore.base_events()`; with none the window starts `lookback_hours` ago, and `since` overrides both. When no window or no column list can be read the readers log why, no metric is written and the test passes. A failed telemetry CALL is a logged warning and never changes the verdict. Example, `data_tests: [icecore.all_columns_anomaly: {exclude_columns: [loaded_at]}]`.

```yaml
data_tests:
  - icecore.all_columns_anomaly
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | relation | required | The relation under test, filled in by dbt. |
| `exclude_columns` | list | `none` | Columns to skip, matched like column names in SQL (a plain name is upper case, a double-quoted one is exact). Optional. |
| `metrics` | list | `none` | Metrics per column, from window_rows, null_rate, avg, min and max. Default `[window_rows, null_rate]`. |
| `bounds` | dictionary | `none` | Static limits per metric, a mapping of metric to min and max numbers; every metric named must be in `metrics`. Optional. |
| `since` | string | `none` | ISO 8601 start of the window. Optional; overrides the previous observation. |
| `timestamp_column` | string | `none` | Date or timestamp column used when CHANGES is unavailable. Optional. |
| `lookback_hours` | number | `24` | Window length when there is no previous observation and no `since`. Default 24. |

## `icecore.column_anomaly`

Incremental column metrics test. Reads only the rows that changed since the previous observation (CHANGES, or `timestamp_column` when change tracking does not cover the window), computes the requested metrics over them, writes each as a metric span event through `icecore.emit_metric` (one per metric, with the column), and fails when a value is outside its static `bounds`. Until the anomaly engines are wired in (UC3) the bounds are the only verdict, and the scoring hook is marked in the test body and not called. The window end is read from Snowflake's clock before the read and recorded on every metric (`dbt.window.until`), and the next run starts its window there, so no row falls between two runs. The previous observation is the newest metric record of the tested column, read through `icecore.base_events()`; with none the window starts `lookback_hours` ago, and `since` overrides both. A value that is null (an empty window, or no non-null values) is not written and not checked. When no window can be read (no change tracking and no usable timestamp column, a missing column) the readers log why, no metric is written and the test passes. A failed telemetry CALL is a logged warning and never changes the verdict. CHANGES is cheap for insert-only windows and can cost more than a full scan after deletes, updates or merges (spike S5). Example, `columns: [{name: amount, data_tests: [icecore.column_anomaly: {metrics: [null_rate, avg, max], bounds: {null_rate: {max: 0.05}}}]}]`.

```yaml
columns:
  - name: <column>
    data_tests:
      - icecore.column_anomaly
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | relation | required | The relation under test, filled in by dbt. |
| `column_name` | string | required | The tested column, filled in by dbt. |
| `metrics` | list | `none` | Metrics to compute, from window_rows, null_rate, avg, min and max (avg, min and max need a numeric column). Default `[window_rows, null_rate]`. |
| `bounds` | dictionary | `none` | Static limits per metric, `{metric: {min: n, max: n}}` with at least one of the two. Every metric named here must be in `metrics`. Optional; without it the test only records the metrics and passes. |
| `since` | string | `none` | ISO 8601 start of the window, for example `2026-10-31T08:00:00.000000Z`. Optional; overrides the previous observation. |
| `timestamp_column` | string | `none` | Date or timestamp column used when CHANGES is unavailable. Optional. |
| `lookback_hours` | number | `24` | Window length when there is no previous observation and no `since`. Default 24. |
| `window_tag` | string | `none` | Name of this test's window. Two column_anomaly tests on the same column and relation should each have their own, so one test's window end does not move the other's. Optional; without it the tests of a column share one window. |

## `icecore.dimension_anomaly`

Incremental dimension test, applied to the column that holds the dimension (for example country). Reads only the rows that changed since the previous observation (CHANGES, or `timestamp_column` when change tracking does not cover the window), counts them per value of the column, and writes one `window_rows` metric per value through `icecore.emit_metric` with the dimension `<column>=<value>`. At most `max_values` values are written (default `vars: icecore: dimension_max_values`, 20, the most frequent ones); the rest are grouped in one `<column>=<other>` metric, and a null value is written as `<column>=<null>`. It fails when a count is outside the static `bounds`. Until the anomaly engines are wired in (UC3) the bounds are the only verdict, and the scoring hook is marked in the test body and not called. The previous observation is the newest dimension metric record of the column, read through `icecore.base_events()`; with none the window starts `lookback_hours` ago, and `since` overrides both. When no window can be read the readers log why, no metric is written and the test passes. A failed telemetry CALL is a logged warning and never changes the verdict. CHANGES is cheap for insert-only windows and can cost more than a full scan after deletes, updates or merges (spike S5). Example, `columns: [{name: country, data_tests: [icecore.dimension_anomaly: {bounds: {min: 1}}]}]`.

```yaml
columns:
  - name: <column>
    data_tests:
      - icecore.dimension_anomaly
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | relation | required | The relation under test, filled in by dbt. |
| `column_name` | string | required | The dimension column, filled in by dbt. |
| `bounds` | dictionary | `none` | Static limits for the count of changed rows of each value, a mapping with min and or max numbers. Optional. |
| `max_values` | integer | `none` | Number of distinct values written before the rest are grouped as other. Optional. |
| `since` | string | `none` | ISO 8601 start of the window, for example `2026-10-31T08:00:00.000000Z`. Optional; overrides the previous observation. |
| `timestamp_column` | string | `none` | Date or timestamp column used when CHANGES is unavailable. Optional. |
| `lookback_hours` | number | `24` | Window length when there is no previous observation and no `since`. Default 24. |

## `icecore.freshness`

Zero-scan freshness test. Reads the time of the last committed change of the tested relation from SYSTEM$LAST_CHANGE_COMMIT_TIME (no data scan), measures the lag against Snowflake's clock, writes it as the metric `freshness_lag_seconds` through `icecore.emit_metric`, and fails when the lag is above `max_lag`. Without `max_lag` it only records the metric and always passes. The verdict comes only from that comparison: a failed telemetry CALL is a logged warning and never changes the result. When no last change time can be read (never written, or not readable) the reader logs why and no metric is written; the test passes without `max_lag`, and with `max_lag` it returns one row `unmeasured: <reason>` so its severity applies. The scoring hook for UC3 (`icecore.score`) is marked in the test body and not called yet. Use `severity: warn` in the test config to warn instead of failing. Example, `data_tests: [icecore.freshness: {max_lag: 2h}]`.

```yaml
data_tests:
  - icecore.freshness
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | relation | required | The relation under test, filled in by dbt. |
| `max_lag` | string | `none` | Largest accepted lag, a number of seconds or a number with a unit s, m, h or d, for example 30m, 2h or 1d. Optional. |

## `icecore.schema_changes`

Zero-scan schema drift test. Reads the current columns with SHOW COLUMNS (no data scan) and compares them with the signature the previous run recorded (an `icecore.schema_signature` span event, read back through `icecore.base_events()`), using `icecore.diff_schema`. Every added column, removed column and type change is one failing row and one `icecore.schema_change` span event at level warn, written with the new signature in one guarded CALL, so the next run compares against the new shape and a change is reported once. The first run, or a run with no readable history, passes and only records the signature; the same when the columns cannot be read. A failed telemetry CALL is a logged warning and never changes the verdict. Use `severity: warn` in the test config to warn instead of failing. Example, `data_tests: [icecore.schema_changes]`. Telemetry reaches the history after the ingestion lag (about 8 seconds, at worst 20, spike S3), so two runs closer than that may compare against an older signature.

```yaml
data_tests:
  - icecore.schema_changes
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | relation | required | The relation under test, filled in by dbt. |

## `icecore.volume`

Zero-scan volume test. Reads the total row count of the tested relation from INFORMATION_SCHEMA.TABLES (no data scan), writes it as the metric `row_count` through `icecore.emit_metric`, and fails when it is below `min_rows` or above `max_rows`. With no argument it only records the metric and always passes. The verdict comes only from that comparison: a failed telemetry CALL is a logged warning and never changes the result. Views, external tables and Iceberg tables have no trusted metadata row count: the reader logs why and no metric is written; the test passes without thresholds, and with `min_rows` or `max_rows` it returns one row `unmeasured: <reason>` so its severity applies. The scoring hook for UC3 (`icecore.score`) is marked in the test body and not called yet. Use `severity: warn` in the test config to warn instead of failing. Example, `data_tests: [icecore.volume: {min_rows: 1}]`.

```yaml
data_tests:
  - icecore.volume
```

| Argument | Type | Default | What it does |
| --- | --- | --- | --- |
| `model` | relation | required | The relation under test, filled in by dbt. |
| `min_rows` | integer | `none` | Smallest accepted row count. Optional. |
| `max_rows` | integer | `none` | Largest accepted row count. Optional. |
