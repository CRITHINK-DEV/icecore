# Event-table conventions

This page says what icecore writes to the event table and how to read it. It describes what is merged on `develop`, as written by the code. Where it states a number, it cites the spike that measured it (`S1.md`, `S2.md`, `S3.md` and `GATE1.md`, all in [`spikes/`](../spikes/GATE1.md)).

You do not need this page to use icecore. Read the views instead (see [Configuration](configuration.md#what-setup-creates)). Read this page if you query the event table yourself, share it with other tools, or review what icecore stores.

Features that are not merged are marked **coming** and have no detail here.

## What writes records

`icecore.setup` creates two procedures in `<target_db>.<obs_schema>` (default `ICECORE_OBS`). Both run as their owner. Every icecore record is written by a `CALL` to one of them. Hooks and tests never write any other way ([GATE1.md](../spikes/GATE1.md#decision)).

| | `ICECORE_EMIT` | `ICECORE_EMIT_RUN` |
| --- | --- | --- |
| Language | SQL | Python 3.11 |
| Called | During a run: `start_run`, the freshness and volume tests, `setup`, `doctor` | At `on-run-end`, by `end_run` |
| Record types | `SPAN_EVENT` | `SPAN`, `SPAN_EVENT` (on a span), `LOG` |
| `SCOPE:name` | `global` | `icecore` |
| Identity | Record name starts with `icecore.` | `SCOPE:name = 'icecore'` |
| Limits per `CALL` | 100 events and 512 KB (icecore's rule) | 512 KB (icecore's rule) |

Why two: SQL cannot set `SCOPE`, cannot write LOG attributes and cannot write spans with your own times. Only Python can. SQL is cheaper for small frequent writes ([S1.md](../spikes/S1.md#icecore_emit_run-python)). 100 events in one `ICECORE_EMIT` `CALL` took 0.73 s on the server. 100 `CALL`s of one event took 78.3 s (S1.md, `proc.batch`, `proc.per_event`).

`ICECORE_ACK` and `ICECORE_SCORE` are **coming**.

### Names and the `icecore.` prefix

`ICECORE_EMIT` writes one span event per record, named `icecore.<kind>`. A name given without the prefix gets it. The prefix is the package's identity on that path, because the scope is always `global` (S1.md, "SQL record names").

`ICECORE_EMIT` writes these kinds today:

| Name | Written by | One record per |
| --- | --- | --- |
| `icecore.run_start` | `start_run` | Run |
| `icecore.capabilities` | `setup`, `doctor` | Run of either |
| `icecore.metric` | `icecore.freshness`, `icecore.volume` (through `emit_metric`) | Metric per table per run |
| `icecore.anomaly` | `emit_metric`, when a scored value is an anomaly | Anomaly. Nothing scores yet, so none is written today |
| `icecore.doctor_probe` | `doctor`'s test `CALL`, tagged `icecore.doctor.probe = true` | `doctor` run |

`ICECORE_EMIT_RUN` writes these, all from `end_run`:

| Record type | Name | Written when | One record per |
| --- | --- | --- | --- |
| `SPAN` | `icecore.invocation` | Always | Run |
| `SPAN` | `icecore.node` | A node has complete timing (a skipped node has none) | Node |
| `SPAN_EVENT` | `icecore.query` | `query_events` is on | Query of a node, on that node's span |
| `LOG` | `icecore.kind = node_result` | `node_results` is on | Node |
| `LOG` | `icecore.kind = lineage` | `lineage` is on and the graph has edges | Part of the edge list |
| `LOG` | `icecore.kind = run_end` | Always | Run |

A `LOG` has no `icecore.` name. Its `icecore.kind` attribute is what tells the kinds apart, and its message is in `VALUE`. The level is `error` for a failed node, `warn` for a warning, `info` otherwise. The `run_end` level follows the run's status.

Each `CALL` also writes the procedure's own span, and each Python `CALL` writes about 3 untagged `INFO` LOG rows from the Snowpark session. Both are filtered out below, and both cost ingestion (S3.md, finding 4; S1.md, "Python noise").

## Attributes

All data is in `RECORD_ATTRIBUTES`. Keys with the `dbt.` prefix are facts about the dbt run. Keys with the `icecore.` prefix are the package's own. Snowflake's `snow.` keys are used as Snowflake defines them. Numbers stay numbers and booleans stay booleans. A missing value is left out, not written as null.

### On every record

| Key | Type | Value |
| --- | --- | --- |
| `dbt.invocation_id` | string | dbt's invocation id. It groups a run (see [Traces](#traces)) |
| `dbt.runtime` | string | `fusion` for dbt 2.x, `cloud` when a `DBT_CLOUD_*` variable is set, `snowflake_native` inside `EXECUTE DBT PROJECT`, else `core` |
| `dbt.command` | string | `build`, `run`, `test`, `freshness` for `dbt source freshness`, ... (S3.md, finding 2) |
| `dbt.target`, `dbt.project` | string | Target name, root project name |
| `icecore.version` | string | Installed package version, or `unknown` |
| `icecore.schema_version` | number | Record layout version, currently `1`. See [Schema version](#schema-version) |

### By record kind

Each list shows the keys added to the base keys above. `snow.query.id` as a record attribute is the node's query. The `snow.query.id` in `RESOURCE_ATTRIBUTES` is the emitting `CALL`'s (S1.md, "Record shape").

| Kind | Attributes |
| --- | --- |
| `icecore.run_start` (event) | `dbt.version`, `dbt.invocation.started_at`, `icecore.anomaly_engine`, `icecore.shadow_engine`, `icecore.level`, `icecore.message` |
| `icecore.capabilities` (event) | `icecore.capability.database`, `.edition`, `.edition_source`, `.event_table`, `.event_table_level`, `.log_level`, `.trace_level`, `.cortex`, `.cortex_reason`, `icecore.level`, `icecore.message` |
| `icecore.metric` (event) | `dbt.metric.name`, `dbt.metric.value` (always a number), `dbt.node.relation`, and when given `dbt.metric.column`, `dbt.metric.dimension`, `dbt.anomaly.engine`, `.expected`, `.lower`, `.upper`, `.score`, `.is_anomaly`, plus `icecore.level` |
| `icecore.invocation` (span) | `dbt.version`, `icecore.anomaly_engine`, `icecore.shadow_engine`, `dbt.invocation.started_at`, `.completed_at`, `.duration_s` |
| `icecore.node` (span) | `dbt.node.unique_id`, `.type`, `.name`, `.materialization`, `.relation`, `.started_at`, `.completed_at`, `.duration_s`, `dbt.timing.<phase>.started_at` and `.completed_at` (compile, execute), and for tests `dbt.test.name`, `.type`, `.column`, `.severity` |
| `icecore.query` (span event) | `snow.query.id`, `dbt.node.unique_id` |
| `node_result` (log) | The `dbt.node.*` keys above, `dbt.result.status`, `.rows_affected`, `.execution_time_s`, `.message`, `snow.query.id`, and for tests `dbt.test.*` including `dbt.test.failures`. Message: [`result_message`](configuration.md#result_message) |
| `lineage` (log) | `dbt.lineage.edges` (JSON text of `[parent, child]` pairs), `.edge_count`, `.edges_total`, `.part`, `.parts` |
| `run_end` (log) | `dbt.invocation.status` (`success`, `warn` or `error`), `dbt.invocation.nodes_total`, `.nodes_success`, `.nodes_failed`, `.nodes_warned`, `.nodes_skipped`, the times of `icecore.invocation`, `dbt.version`, the two engine settings |

Also on records icecore built: `icecore.kind` (logs), `icecore.truncated` and `icecore.truncated_keys` (when a value was cut, see [Payload cap](#payload-cap-and-chunking)). Python adds `code.filepath`, `code.function` and `code.lineno` to logs (S1.md, results table).

The metric names written today are `freshness_lag_seconds` and `row_count`.

### Not written yet

The design also names keys for schema changes (`dbt.schema.*`), cost (`dbt.cost.*`), root cause (`dbt.root_cause.*`), failed-row samples (`dbt.sample.*`) and acknowledgements (`icecore.ack.*`). No merged code writes them. They are **coming**. The `SCHEMA_CHANGES` view exists and stays empty until its emitter is merged.

### Schema version

`icecore.schema_version` is the version of the record layout. It is `1`.

The rule:

- A change that gives an existing key a new meaning or type bumps the version.
- A new key does not bump it.
- Views read the version as a number and read every typed column with `TRY_TO_*`, so a key that an older record lacks reads as null and never fails a view.

Nothing branches on a version other than `1` yet. A reader of the raw table should group or filter on it. The version is set in one macro, `_icecore_schema_version`.

## Reading icecore records

### The base filter

Other tools write to the same event table. Keep only icecore's records with this filter. It is what every icecore view starts from (`icecore.base_events()`):

```sql
select *
from <EVENT_TABLE>
where RECORD_TYPE in ('SPAN', 'SPAN_EVENT', 'LOG')
  and RESOURCE_ATTRIBUTES:"snow.database.name"::string   = '<TARGET_DB>'
  and RESOURCE_ATTRIBUTES:"snow.schema.name"::string     = '<OBS_SCHEMA>'
  and RESOURCE_ATTRIBUTES:"snow.executable.name"::string like 'ICECORE_EMIT%'
  and (
    SCOPE:name::string = 'icecore'
    or (RECORD_TYPE = 'SPAN_EVENT' and RECORD:name::string like 'icecore.%')
  );
```

The first three tests on `RESOURCE_ATTRIBUTES` keep environments that share one table apart. The last test is the identity from [Names](#names-and-the-icecore-prefix). It also drops the procedure's own span and the Snowpark noise.

`<EVENT_TABLE>` is the table `setup` resolved with `SHOW PARAMETERS LIKE 'EVENT_TABLE' IN DATABASE <TARGET_DB>`. When that is the account table `SNOWFLAKE.TELEMETRY.EVENTS`, the views read `SNOWFLAKE.TELEMETRY.EVENTS_VIEW`, and readers need the `EVENTS_VIEWER` application role.

Records from native dbt Projects on Snowflake fail this filter. How they are identified is not defined yet.

Checked on the sandbox, a run of 6 nodes, grouped by record:

```sql
select RECORD_TYPE, SCOPE:name::string as scope,
       iff(RECORD:name::string like 'icecore.%', RECORD:name::string, '(log)') as name,
       count(*) as n
from <EVENT_TABLE>
where <the filter above>
group by 1, 2, 3
order by 1, 2, 3;
```

```text
RECORD_TYPE | SCOPE  | NAME                 | N
LOG         | icecore| (log)                | 8
SPAN        | icecore| icecore.invocation   | 1
SPAN        | icecore| icecore.node         | 6
SPAN_EVENT  | global | icecore.capabilities | 1
SPAN_EVENT  | global | icecore.metric       | 2
SPAN_EVENT  | global | icecore.run_start    | 1
SPAN_EVENT  | icecore| icecore.query        | 6
```

The 8 logs are 6 node results, 1 lineage record and 1 run end.

### Status of the latest run

```sql
select RECORD_ATTRIBUTES:"dbt.invocation_id"::string          as invocation_id,
       RECORD_ATTRIBUTES:"dbt.invocation.status"::string      as status,
       RECORD_ATTRIBUTES:"dbt.invocation.nodes_total"::number as nodes,
       VALUE::string                                          as message
from <EVENT_TABLE>
where <the filter above>
  and RECORD_ATTRIBUTES:"icecore.kind"::string = 'run_end'
order by TIMESTAMP desc
limit 1;
```

```text
INVOCATION_ID                        | STATUS  | NODES | MESSAGE
765da2a6-...                         | success | 6     | dbt build finished: success, 6 nodes (6 succeeded, 0 failed, 0 warned, 0 skipped)
```

### Layout versions in the table

```sql
select RECORD_ATTRIBUTES:"icecore.schema_version"::number as schema_version,
       RECORD_ATTRIBUTES:"icecore.version"::string        as icecore_version,
       count(*) as n
from <EVENT_TABLE>
where <the filter above>
group by 1, 2;
```

```text
SCHEMA_VERSION | ICECORE_VERSION | N
1              | 0.0.1           | 25
```

### The views

The views hide all of the above. After a run with the freshness and volume tests:

```sql
select metric, relation, value from <OBS_SCHEMA>.METRICS order by metric;
select parent_unique_id, child_unique_id from <OBS_SCHEMA>.LINEAGE_LATEST;
```

```text
METRIC                | RELATION                             | VALUE
freshness_lag_seconds | "<DB>"."<SCHEMA>"."ORDERS"           | 1.299
row_count             | "<DB>"."<SCHEMA>"."ORDERS"           | 2.0

PARENT_UNIQUE_ID  | CHILD_UNIQUE_ID
model.shop.orders | model.shop.order_summary
```

## Traces

Each `CALL` is its own trace, and every record of one `CALL` shares it. A dbt run therefore has no single trace: a run whose records need several `CALL`s spans several traces (S1.md, "Trace grouping"). Use `dbt.invocation_id` to group a run. Node spans are not parented to the invocation span. Whether they can be, across `CALL`s, is unmeasured (GATE1.md, open item 6).

Records land in the event table of the procedures' database, at the `LOG_LEVEL` and `TRACE_LEVEL` that apply to the icecore schema (its own, else the database's). A session setting does not switch icecore off: with the session at `OFF`, procedure records still landed (S1.md, `proc.levels_off`). Use `telemetry: false` instead.

## Timestamps

### `OBSERVED_TIMESTAMP`

Do not build on `OBSERVED_TIMESTAMP`. S3 found it NULL on all 10,000 `SPAN_EVENT` rows and the span rows it counted (S3.md, finding 3). A view cannot measure ingestion lag from it. Lag can only be measured from outside. S3 measured a p95 of 7.73 s and 6.12 s over two bursts of 10,000 records, with a worst burst record at 8.73 s and a worst hook record at 20.3 s (S3.md, headline), and readers use a fixed delay: the `history_delay_minutes` var.

One more observation, not covered by S3. On the sandbox, the `LOG` records of the end-of-run `CALL` have `OBSERVED_TIMESTAMP` set, equal to `TIMESTAMP` in all 8 rows. It carries no lag either, so the same rule holds.

`START_TIMESTAMP` is set on the spans `ICECORE_EMIT_RUN` writes, and NULL on events and logs.

### The dbt clock and the Snowflake clock

Two clocks stamp icecore records:

- **dbt's clock.** `ICECORE_EMIT_RUN` writes each span with the start and end times from dbt's own result, kept to the millisecond (S1.md, `proc.emit_run_python`). The same times are attributes: `dbt.invocation.started_at`, `dbt.node.started_at`, and so on, as ISO-8601 UTC text.
- **Snowflake's clock.** `TIMESTAMP` on an `ICECORE_EMIT` event is the time of the `CALL`. Its dbt time is only in an attribute. The freshness test and `doctor` also measure against Snowflake's clock.

The two clocks need not agree. On the sandbox, the `icecore.run_start` event of one run was stamped `12:28:58` by Snowflake, while the same record's `dbt.invocation.started_at` says `12:29:06`: the two clocks differ by at least 8 s in that run. That is one measurement, and the cause was not found. Do not subtract a dbt time from a `TIMESTAMP` without allowing for it. Use dbt's attributes to compare dbt events with each other, and `TIMESTAMP` to compare Snowflake events with each other.

Spans are written after the fact and keep their explicit times: spans 2 hours back and, in an exploration, 5 days back were stored with them (S1.md, "Timestamps written after the fact"). A reader that windows on `TIMESTAMP` alone can miss them.

## Nested values

`ICECORE_EMIT` is written with `SYSTEM$ADD_EVENT`, which silently drops an attribute that is an object or a list. `icecore.emit` therefore writes a nested value as JSON text, a string. Read it with `try_parse_json`.

`ICECORE_EMIT_RUN` log records keep native nesting, but the lineage record uses the same text form for its edge list:

```sql
select RECORD_ATTRIBUTES:"dbt.lineage.edges"::string                                  as text,
       typeof(RECORD_ATTRIBUTES:"dbt.lineage.edges")                                  as type,
       try_parse_json(RECORD_ATTRIBUTES:"dbt.lineage.edges"::string)[0][0]::string     as first_parent
from <EVENT_TABLE>
where <the filter above>
  and RECORD_ATTRIBUTES:"icecore.kind"::string = 'lineage'
limit 1;
```

```text
TEXT                                                  | TYPE    | FIRST_PARENT
[["model.shop.orders", "model.shop.order_summary"]]   | VARCHAR | model.shop.orders
```

## Payload cap and chunking

Snowflake limits one record, and it fails quietly. At 1 MB a record is dropped, and the `CALL` still returns `ok` (S2.md, `threshold.proc_event.1024`). A Python log message is cut to 524,252 characters with no flag from 512 KB up (S2.md, `threshold.python_log.512`).

icecore keeps well under these limits:

- **Cap per record.** `payload_cap_kb`, default 64, clamped to 1 to 256 (GATE1.md, "Payload cap"). Over the cap, icecore cuts the longest string values until the record fits, and sets `icecore.truncated = true` and `icecore.truncated_keys`. The cut is never silent.
- **Large values** go in attributes, not in log messages.
- **Chunks.** `ICECORE_EMIT`: at most 100 events and 512 KB per `CALL`. A `CALL` keeps only its last 128 events, and drops all of them when the kept events total about 1 MB, with no drop count (S2.md, `proc_batch.129x1`, `proc_batch.16x64`). `ICECORE_EMIT_RUN`: at most 512 KB per `CALL`. 200 spans and 4 MB landed whole in S2, but the 4 MB `CALL` took 153.81 s of dbt time against 5.30 s for 200 KB (S2.md, `emit_run_batch.64x64`), so the limit is for dbt time. The size is measured on the escaped statement, as it is sent.
- **Never one `CALL` per event.**

If a `CALL` fails, dbt prints one warning per hook and carries on (see [Configuration](configuration.md#hooks)).

## Security rules

The event table may be readable by more people than your data. icecore's rules follow from that.

- **Result messages are redacted by default.** dbt's message can quote data values from a Snowflake error. `result_message: redacted` replaces quoted literals with `'?'` and cuts the text at 1,000 characters. `full` and `none` are the other modes. See [Configuration](configuration.md#result_message).
- **Failed-row samples are opt-in.** Samples are **coming**. When they ship, they stay off until you turn them on.
- **Acknowledgements go only through `ICECORE_ACK`.** The procedure and the app are **coming**. No other path will write an acknowledgement.
- **No tables, no INSERT.** icecore creates no table and writes no row any other way than the two procedures.
- **The procedures run as their owner.** The role that runs dbt needs USAGE on them, not write access to the event table. Writing needs no `EVENTS_VIEWER`. Reading does, when the views read the account table (GATE1.md, "What setup creates").
- **Read views, not the table.** The views are the public interface. They apply the base filter for you.
