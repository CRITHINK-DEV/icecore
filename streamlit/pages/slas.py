"""SLAs: declared schedules against actual runs, silence events."""
import streamlit as st

from lib import queries, slas
from lib.ui import empty_state, page_header

page_header("SLAs", "Which contracts are breached.", ["sla_declared", "silence_events", "node_executions"])

try:
    declared = queries.sla_declared()
    silences = queries.silence_events() if declared else []
    nodes = queries.node_executions(limit=queries.MAX_LIMIT) if declared else []
except Exception as exc:  # no Snowpark session (offline) or the views are not created yet: show the empty state
    declared, load_error = [], exc
else:
    load_error = None
if not declared:
    empty_state(slas.EMPTY_MESSAGE)
    st.code(slas.SLA_EXAMPLE, language="yaml")
    if load_error is not None:
        st.caption(f"Reading the views failed: {load_error}")
    st.stop()

st.subheader("Declared and actual schedules")
st.dataframe(slas.sla_schedule_table(declared, silences, nodes),
             use_container_width=True, hide_index=True)

st.subheader("Silence events")
if silences:
    st.dataframe(slas.silence_table(silences), use_container_width=True, hide_index=True)
else:
    st.caption("No missed runs.")
