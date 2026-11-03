"""Cost: what to optimise. Credits in the window, per model with a trend, and the top regressions."""
from datetime import datetime, timedelta

import streamlit as st

from lib import charts, console, cost, queries
from lib.pages import cost as logic

console.page("Cost", "What to optimise.", ["invocations", "node_executions", "metrics", "alerts_feed"])

filters = console.filter_bar("cost", layers=None, statuses=False)

since = datetime.now() - timedelta(days=cost.TREND_DAYS + 1)
invocations, err_i = console.guarded(lambda: queries.invocations(since=since, limit=5000))
nodes, err_n = console.guarded(lambda: queries.node_executions(since=since, limit=queries.MAX_LIMIT))
metric_rows, err_m = console.guarded(lambda: queries.metrics(metric="credits", since=since, limit=queries.MAX_LIMIT))
alerts, err_a = console.guarded(lambda: queries.alerts_feed(kind="cost", since=since))

if not invocations and not nodes:
    console.empty("No cost data yet. Set `cost: true` under `vars: icecore:` in dbt_project.yml, "
                  "run `dbt run-operation icecore.setup`, then run dbt.", err_i or err_n)

now = logic.now_of(invocations, nodes)
console.kpi_row(logic.tiles(invocations, nodes, now=now, days=filters.days))
for err in (err_n, err_m, err_a):
    if err:
        st.caption(f"Reading the views failed: {err}")

st.altair_chart(charts.daily(cost.daily_trend(invocations, now), x="day", y="credits",
                             title="Credits per day, last 30 days", y_title="Credits"), use_container_width=True)

st.subheader("Credits per model")
models = logic.per_model(nodes, now=now)
console.table(
    models[:50], key="cost_models", limit=None,
    column_config={
        "unique_id": None, "type": None,
        "name": st.column_config.TextColumn("Model"),
        "credits": st.column_config.NumberColumn("Credits", format="%.4f"),
        "runs": st.column_config.NumberColumn("Runs"),
        "avg_credits": st.column_config.NumberColumn("Avg per run", format="%.4f"),
        "trend": st.column_config.LineChartColumn(f"Last {logic.TREND_POINTS} days"),
        "attribution": st.column_config.TextColumn("Attribution"),
    })

st.subheader("Top regressions")
regs = logic.regressions(metric_rows, alerts)
if regs:
    console.table(
        regs, key="cost_regressions",
        column_config={
            "unique_id": None,
            "relation": st.column_config.TextColumn("Model"),
            "observed_at": st.column_config.DatetimeColumn("When"),
            "credits": st.column_config.NumberColumn("Credits", format="%.4f"),
            "expected": st.column_config.NumberColumn("Expected", format="%.4f"),
            "excess": st.column_config.NumberColumn("Over", format="%.4f"),
            "detail": st.column_config.TextColumn("Engine / alert"),
            "source": st.column_config.TextColumn("Source"),
        })
else:
    st.caption("No cost regressions in the last 30 days. They appear once enough credits history exists for the "
               "engines to score the `credits` metric.")
