"""Schedules & freshness: declared schedules against what ran, missed runs, and source lag against its limit."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import charts, console, queries, slas
from lib.pages import schedules as logic

console.page("Schedules & freshness", "Did the declared schedules and sources arrive on time.",
             ["sla_declared", "silence_events", "node_executions", "metrics"])
filters = console.filter_bar("schedules", layers=None, statuses=False)
since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=filters.days)

declared, e1 = console.guarded(lambda: queries.sla_declared())
silences, e2 = console.guarded(lambda: queries.silence_events())
nodes, e3 = console.guarded(lambda: queries.node_executions(limit=queries.MAX_LIMIT))
metrics, e4 = console.guarded(lambda: queries.metrics(metric=logic.LAG_METRIC, since=since, limit=queries.MAX_LIMIT))
error = e1 or e2 or e3 or e4
if error:
    console.empty(slas.EMPTY_MESSAGE, error)

schedule_rows = logic.schedules_table(declared, silences, nodes)
sources = logic.sources_table(metrics)
if not declared and not sources:
    console.empty(slas.EMPTY_MESSAGE, stop=False)
    st.code(slas.SLA_EXAMPLE, language="yaml")
    st.stop()

for note in (console.truncation_note(nodes, queries.MAX_LIMIT), console.truncation_note(metrics, queries.MAX_LIMIT)):
    if note:
        st.caption(note)

console.kpi_row(logic.tiles(schedule_rows, sources))

st.subheader("Declared schedules")
if schedule_rows:
    console.table(schedule_rows, {
        "unique_id": st.column_config.TextColumn("Model"),
        "status": st.column_config.TextColumn("Status"),
        "declared_every": st.column_config.TextColumn("Declared every"),
        "grace": st.column_config.TextColumn("Grace"),
        "actual_every": st.column_config.TextColumn("Actual cadence", help="Median gap between successful runs."),
        "runs_observed": st.column_config.NumberColumn("Runs seen"),
        "last_success_at": st.column_config.DatetimeColumn("Last success"),
        "silent_now": st.column_config.CheckboxColumn("Silent now"),
        "channel": None, "severity": None, "slower_than_declared": None,
    }, key="schedules_table")
else:
    console.empty(slas.EMPTY_MESSAGE, stop=False)
    st.code(slas.SLA_EXAMPLE, language="yaml")

st.subheader("Missed runs")
missed = logic.missed_runs(silences, since)
if missed:
    console.table(missed, {
        "unique_id": st.column_config.TextColumn("Model"),
        "expected_at": st.column_config.DatetimeColumn("Expected at"),
        "last_success_at": st.column_config.DatetimeColumn("Last success"),
        "detected_at": st.column_config.DatetimeColumn("Detected at"),
        "grace": st.column_config.TextColumn("Grace"),
        "severity": st.column_config.TextColumn("Severity"),
        "channel": st.column_config.TextColumn("Channel"),
    }, key="schedules_missed")
else:
    st.caption("No missed runs in this window.")

st.subheader("Source freshness")
if sources:
    console.chart(charts.lag_vs_limit(logic.chart_rows(sources)), sources)
    console.table(sources, {
        "name": st.column_config.TextColumn("Source"),
        "status": st.column_config.TextColumn("Status"),
        "data_age": st.column_config.TextColumn("Data age"),
        "limit": st.column_config.TextColumn("Limit"),
        "relation": st.column_config.TextColumn("Relation"),
        "observed_at": st.column_config.DatetimeColumn("Measured at"),
        "unique_id": None, "lag_minutes": None, "threshold_minutes": None, "over_limit": None,
    }, key="schedules_sources")
else:
    st.caption("No source freshness recorded in this window.")
