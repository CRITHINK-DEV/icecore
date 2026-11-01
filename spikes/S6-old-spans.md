# S6: old spans and the event table (story 2501518a)

Measured 1 Nov 2026 in `ICECORE_SANDBOX`, with `integration_tests/tests/spikes/test_s6_old_spans.py`; numbers in
`results/s6.json` (`cutoff`).

## Question

PR #84 (scale scenarios) reported that `ICECORE_EMIT_RUN` returns `ok` but node spans whose times are hours old do not land.
At what age does each record kind stop landing?

## Method

An obs schema created with `icecore.create_emit_procedures(schema)`, one `CALL` of `ICECORE_EMIT_RUN` per age (5 min, 30 min,
1 h, 2 h, 3 h, 4 h, 6 h, 8 h, 12 h, 18 h, 24 h, 48 h, 3 d, 7 d, 30 d), each with: a span entirely at that age; a span that
started at that age and ended now; a span stamped now with a span event at that age. One LOG record. Read back from the event
table, polling on Snowflake's clock (a 35-day `TIMESTAMP` window), after the first rows landed plus 90 s (ingestion lag p95
8 s, worst 20 s, S3).

## Result: no cut-off

| Record | Ages tried | Landed |
|---|---|---|
| Span, start and end at that age | 5 min to 30 days | 15 of 15 |
| Span, start at that age, end now | 5 min to 30 days | 15 of 15 |
| Span event at that age, on a span stamped now | 5 min to 30 days | 15 of 15 |
| LOG | now | 1 of 1 |

Every `CALL` returned `ok spans=3 logs=0`. S1 had already seen 2 hours and 5 days; this extends it to 30 days. The start and end
times were kept to the millisecond (asserted for the 4 node spans of `test_old_spans.py`, 30 h to 1 h old).

LOG records have no time argument in `ICECORE_EMIT_RUN` (Python `logging` stamps them when the `CALL` runs), so an explicit
LOG time cannot be sent; icecore never needs one, since its LOGs are written at on-run-end and dbt's times ride in attributes.

## What PR #84 saw

Nothing was dropped. The scale test read the run back with `snowflake_since(sf)`, a 10-minute window on `TIMESTAMP`. A span's
`TIMESTAMP` is its end time, so a node that ended hours ago is outside that window. With the same 30-day data, a 10-minute
window returned 2 rows per age (the span ending now and the span stamped now) and never the two records at the old age
(`rows_seen_with_a_10_minute_TIMESTAMP_window_per_age_min`). The records were in the table; the reader did not look there.

## Mitigation

None is needed in the writer: node spans keep dbt's times as their own start and end, and as `dbt.node.started_at` /
`dbt.node.completed_at`, so `end_run`, `emit_run` and `spans.sql` are unchanged and there is no fallback to warn about.

- `tests/test_old_spans.py` pins it: a simulated long run (nodes 30 h, 12 h, 3 h and 1 h old) sends every node span, node result
  and the run-end LOG, with dbt's times, and all land. It also shows the cause: a 10-minute `TIMESTAMP` window does not see
  the old node spans.
- Rule for readers and tests: window on a time that covers the run (the invocation's duration plus the lag), or on
  `dbt.invocation_id`, never on a short `TIMESTAMP` window for node spans. The views read dbt's times from attributes already
  and filter on database, schema and executable name; none of them windows on `TIMESTAMP` for node spans.
- The scale scenario's workaround (recent timings, "the event table drops spans that are hours old") rests on this misreading and
  can use real timings with a wide `since`.
