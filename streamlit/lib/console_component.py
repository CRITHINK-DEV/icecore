"""The Console: prototype D (docs/design/app-prototype/console.html) as one bundled Streamlit component.

The React app is a committed Vite build in components/console/build (no CDN, fonts bundled). Python sends the data
(the ICECORE_DATA contract: project, generated_at, nodes, runs, node_executions, test_results, metrics,
source_freshness, schema_changes, slas, incidents, capabilities, plus `errors`, a list of {view, error}) and an
optional `notice` shown once as a toast. Navigation, node pages, lineage, drawers and the theme stay in the browser.

`render` returns the browser's action, once, or None:
  {"action": "ack", "incident_key": str, "nonce": str}      Acknowledge clicked
  {"action": "refresh", "nonce": str}                        Refresh clicked
  {"action": "filters", "days": int, "layer": str, "nonce": str}   window or layer changed (Overview, Runs, Cost)
A component keeps its last value across reruns, so the same nonce is returned only the first time.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
from pathlib import Path
from typing import Any

BUILD_DIR = Path(__file__).resolve().parent.parent / "components" / "console" / "build"
COMPONENT_NAME = "icecore_console"

_component = None


def _json_default(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    return str(value)


def to_json_safe(data: dict) -> dict:
    """The payload as plain JSON types: Decimal -> float, datetime/date -> ISO text, anything else -> str."""
    return json.loads(json.dumps(data, default=_json_default))


def component():
    """The declared component (declared once, from the build directory)."""
    global _component
    if _component is None:
        import streamlit.components.v1 as components

        _component = components.declare_component(COMPONENT_NAME, path=str(BUILD_DIR))
    return _component


def render(data: dict, *, height: int = 900, notice: str | None = None, key: str = "icecore_console") -> dict | None:
    """Draw the Console filling `height` pixels; return the new browser action (see module doc) or None."""
    import streamlit as st

    value = component()(data=to_json_safe(data or {}), height=int(height), notice=notice, key=key, default=None)
    if not isinstance(value, dict) or not value.get("action"):
        return None
    seen = f"{key}__nonce"
    if st.session_state.get(seen) == value.get("nonce"):
        return None
    st.session_state[seen] = value.get("nonce")
    return value
