"""Tests & anomalies: tiles, the band chart of the selected metric (two side by side when both engines scored), and
the test results, failures first, with the engine that scored each."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from lib import charts, console, queries
from lib.pages import tests as logic

console.page("Tests & anomalies", "What to act on, which engine to trust.", ["test_results", "metrics"])

filters = console.filter_bar("tests", layers=None)
since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=filters.days)
results, results_error = console.guarded(lambda: queries.test_results(since=since, limit=queries.MAX_LIMIT))
metrics, metrics_error = console.guarded(lambda: queries.metrics(since=since, limit=queries.MAX_LIMIT))

if not results and not metrics:
    console.empty("No test results or monitored metrics yet. Add icecore tests (`icecore.volume`, "
                  "`icecore.freshness`, `icecore.schema_changes`) to your models, run "
                  "`dbt run-operation icecore.setup`, then `dbt test`.", results_error or metrics_error)
if results_error or metrics_error:
    st.caption(f"Reading part of the views failed: {results_error or metrics_error}")

console.kpi_row(logic.tiles(results, metrics))

st.subheader("Monitored metric")
series = logic.series_list(metrics)
if not series:
    st.caption("No monitored metric in this window.")
else:
    by_label = {f"{'🔴 ' if s['anomalous'] else ''}{s['label']}  [{s['engine']}]": s for s in series}
    chosen = by_label[st.selectbox("Metric", list(by_label), key="tests_series")]
    by_engine = logic.series_for(metrics, chosen["relation"], chosen["metric"], chosen["column"], chosen["dimension"])
    columns = st.columns(len(by_engine)) if len(by_engine) > 1 else [st.container()]
    for col, (engine, points) in zip(columns, by_engine.items()):
        with col:
            st.altair_chart(charts.band_chart(points, title=chosen["label"], unit=chosen["metric"],
                                              engine=None if engine == logic.UNSCORED else engine),
                            use_container_width=True)
            st.caption(logic.score_caption(points, engine))
    if len(by_engine) > 1:
        st.caption("Both engines scored this metric (shadow scoring): the primary engine decides the verdict.")

st.subheader("Test results")
rows = logic.filter_status(logic.results_table(results, metrics), filters.statuses)
console.table(rows, {
    "unique_id": None, "executed_at": st.column_config.DatetimeColumn("Executed (UTC)"),
    "score": st.column_config.NumberColumn("Score", format="%.2f"),
    "root_cause": st.column_config.TextColumn("Root cause"),
}, key="tests_table", limit=queries.MAX_LIMIT)
