import logging
import os
import sys

import streamlit as st
import streamlit.components.v1 as components

log = logging.getLogger("s14")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler(sys.stdout))

st.title("S14 component probe")
st.write("streamlit", st.__version__, "python", sys.version.split()[0])

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
try:
    comp = components.declare_component("s14_probe", path=path)
    value = comp(label="hello", key="probe", default=None)
    st.write("component value:", value)
    log.info("S14 component_value=%s", value)
except Exception as exc:  # noqa: BLE001 - spike: record any failure
    st.error(f"declare/render failed: {type(exc).__name__}: {exc}")
    log.error("S14 declare_failed %s: %s", type(exc).__name__, exc)
