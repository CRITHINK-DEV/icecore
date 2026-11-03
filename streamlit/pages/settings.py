"""Settings & about: what this account can do, the versions in use, and where alerts go."""
import streamlit as st

from lib import console, queries, settings
from lib.pages import settings as logic

console.page("Settings & about", "What this account can do, the versions in use, and where alerts go.",
             ["capabilities", "invocations", "sla_declared", "silence_events"])

cap_rows, cap_err = console.guarded(lambda: queries.capabilities(limit=1))
runs, runs_err = console.guarded(lambda: queries.invocations(limit=200))
declared, decl_err = console.guarded(queries.sla_declared)
silences, sil_err = console.guarded(lambda: queries.silence_events(limit=queries.MAX_LIMIT))

cap = logic.latest_capabilities(cap_rows)
channels = logic.channel_rows(declared, silences)
errors = [e for e in (cap_err, runs_err, decl_err, sil_err) if e]

st.caption(f"Views schema in use: `{settings.views_schema()}` (change it in the sidebar).")
if not cap and not runs and not channels:
    console.empty(logic.EMPTY_MESSAGE, errors[0] if errors else None)
for err in errors:
    st.caption(f"Reading the views failed: {err}")

console.kpi_row(logic.tiles(cap, channels))

left, right = st.columns(2)
with left:
    st.subheader("Capabilities")
    if cap:
        st.dataframe([{"Setting": k, "Value": v} for k, v in logic.capability_rows(cap)], hide_index=True,
                     width="stretch")
    else:
        st.caption("Nothing detected yet. " + logic.EMPTY_MESSAGE)
with right:
    st.subheader("Alert channels")
    if channels:
        st.dataframe(channels, hide_index=True, width="stretch", column_config={
            "channel": "Channel", "models": "Models", "last_silence_at": "Last silence",
            "last_notified_at": "Last notification"})
    else:
        st.caption("No channel declared (`meta.alerts.channel`).")
    st.caption(logic.CHANNEL_NOTE)

st.subheader("Versions per run")
if runs:
    st.dataframe(logic.version_rows(runs), hide_index=True, width="stretch", column_config={
        "started_at": "Started", "project": "Project", "target": "Target", "invocation_id": "Invocation",
        "dbt": "dbt", "icecore": "icecore"})
    st.caption(logic.ICECORE_VERSION_NOTE)
else:
    st.caption("No runs recorded yet.")
