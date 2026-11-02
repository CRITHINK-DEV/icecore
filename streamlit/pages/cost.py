"""Cost: credits per model, per run, trend, top regressions."""
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from lib import cost, queries
from lib.ui import empty_state, page_header

page_header("Cost", "What to optimise.", ["node_executions", "invocations", "metrics"])

since = datetime.now() - timedelta(days=cost.TREND_DAYS)
try:
    invocation_rows = queries.invocations(since=since, limit=5000)
    models = cost.credits_per_model(queries.node_executions(since=since, limit=queries.MAX_LIMIT))
except Exception as exc:  # no Snowpark session (offline) or the views are not created yet: show the empty state
    invocation_rows, models, load_error = [], [], str(exc)
else:
    load_error = None
starts = [r["started_at"] for r in invocation_rows if r.get("started_at")]
now = max(starts) if starts else datetime.now()  # day buckets follow the dbt host's clock, not the app's
runs = cost.credits_per_run(invocation_rows)

if not runs and not models:
    empty_state(
        "No cost data yet. Set `cost: true` under `vars: icecore:` in dbt_project.yml, "
        "run `dbt run-operation icecore.setup`, then run dbt."
    )
    if load_error:
        st.caption(f"Reading the views failed: {load_error}")
    st.stop()

st.subheader("Trend, last 30 days")
trend = pd.DataFrame(cost.daily_trend(invocation_rows, now))
st.altair_chart(
    alt.Chart(trend, title="Credits per day").mark_bar().encode(
        x=alt.X("day:T", title="Day"), y=alt.Y("credits:Q", title="Credits"),
        tooltip=["day:T", "credits:Q"]),
    use_container_width=True)

st.subheader("Credits per model")
st.dataframe(pd.DataFrame(models[:50]), use_container_width=True, hide_index=True,
             column_order=["name", "type", "credits", "runs", "avg_credits"])

st.subheader("Credits per run")
st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)

st.subheader("Top cost regressions")
regressions = cost.top_regressions(queries.metrics(metric="credits", since=since, limit=queries.MAX_LIMIT))
if regressions:
    st.dataframe(pd.DataFrame(regressions), use_container_width=True, hide_index=True)
else:
    empty_state("No cost regressions in the last 30 days. They appear once enough credits history exists for the "
                "engines to score the `credits` metric.")
