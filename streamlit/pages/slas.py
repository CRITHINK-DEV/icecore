"""SLAs: declared schedules against actual runs, silence events."""
import streamlit as st

from lib import queries, slas
from lib.ui import empty_state, page_header

page_header("SLAs", "Which contracts are breached.", ["sla_declared", "silence_events", "node_executions"])

declared = queries.sla_declared()
if not declared:
    empty_state(slas.EMPTY_MESSAGE)
    st.code(slas.SLA_EXAMPLE, language="yaml")
    st.stop()

silences = queries.silence_events()

st.subheader("Declared and actual schedules")
st.dataframe(slas.sla_schedule_table(declared, silences, queries.node_executions(limit=queries.MAX_LIMIT)),
             use_container_width=True, hide_index=True)

st.subheader("Silence events")
if silences:
    st.dataframe(slas.silence_table(silences), use_container_width=True, hide_index=True)
else:
    st.caption("No missed runs.")
