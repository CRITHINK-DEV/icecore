"""icecore app: the Console (prototype D) as one full-page Streamlit component.

Runs as a Streamlit in Snowflake object, or locally. The page reads the icecore views in the schema given by
ICECORE_VIEWS_SCHEMA (default <current database>.ICECORE_OBS; see lib/settings.py), never the raw event table.
Navigation, node pages, lineage and the theme live in the browser component; this host only fetches the data,
passes it in, and handles the two actions the component returns (ack, refresh).
"""
import time

import streamlit as st

from lib import ack, console_component, console_data, queries, settings

PENDING_KEY = "icecore_console_pending_acks"   # incident_key -> user, until a build shows it acknowledged
FETCH_DAYS = 30        # the browser filters the window (7/15/30 days), so the server always reads the widest one
CACHE_TTL_S = 300
PENDING_ACK_TTL_S = 120   # the views show an ack within seconds; past this the patch stops and the views are trusted
NOTICE_KEY = "icecore_console_notice"

st.set_page_config(page_title="icecore", page_icon=":material/monitoring:", layout="wide",
                   initial_sidebar_state="collapsed")

# Host chrome only: the component fills the page, so Streamlit's own header, toolbar, sidebar and padding go.
st.markdown("""<style>
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"], [data-testid="stDecoration"], footer {display: none !important;}
[data-testid="stMainBlockContainer"], .block-container {padding: 0 !important; max-width: 100% !important;}
[data-testid="stAppViewContainer"] > .main, section.main {overflow: hidden;}
iframe[title*="icecore_console"] {display: block; border: 0;}
</style>""", unsafe_allow_html=True)

EMPTY = {"project": None, "generated_at": None, "nodes": [], "runs": [], "node_executions": [], "test_results": [],
         "metrics": [], "source_freshness": [], "schema_changes": [], "slas": [], "incidents": [],
         "capabilities": {}, "errors": []}


@st.cache_data(ttl=CACHE_TTL_S, show_spinner=False)
def _build(days: int, views_schema: str) -> dict:
    """The data object; `views_schema` is only part of the cache key (the query layer reads it from settings)."""
    return console_data.build(days=days)


def _current_user() -> str:
    return queries.current_user() or "you"


def _with_pending_acks(data: dict) -> dict:
    """The views lag the acknowledgement by a few seconds (event-table delay): until a build shows an incident
    acknowledged, and for PENDING_ACK_TTL_S at most, show it acknowledged by its user. The cached object is copied,
    never changed."""
    now = time.time()
    pending = {k: v for k, v in (st.session_state.get(PENDING_KEY) or {}).items()
               if now - v["at"] < PENDING_ACK_TTL_S}
    if not pending:
        st.session_state[PENDING_KEY] = {}
        return data
    incidents, still = [], {}
    for inc in data.get("incidents") or []:
        key = inc.get("incident_key")
        if key in pending and not inc.get("acknowledged_by"):  # a build that shows it acknowledged ends the patch
            still[key] = pending[key]
            if inc.get("state") == "open":
                inc = {**inc, "state": "acknowledged", "acknowledged_by": pending[key]["user"]}
        incidents.append(inc)
    st.session_state[PENDING_KEY] = still
    return {**data, "incidents": incidents}


def _data() -> dict:
    try:
        return _with_pending_acks(_build(FETCH_DAYS, settings.views_schema()))
    except Exception as exc:  # the component still renders, with the error in its data-quality list
        return {**EMPTY, "errors": [{"view": "app", "error": str(exc)}]}


def _handle(action: dict | None) -> None:
    if not action:
        return
    kind = action.get("action")
    if kind == "ack":
        result = ack.acknowledge(action.get("incident_key"))
        _build.clear()
        if result.ok:
            st.session_state[PENDING_KEY] = {**(st.session_state.get(PENDING_KEY) or {}),
                                             action.get("incident_key"): {"user": _current_user(),
                                                                                       "at": time.time()}}
        st.session_state[NOTICE_KEY] = result.message
        st.rerun()
    elif kind == "refresh":
        _build.clear()
        st.rerun()
    # Any other action is a no-op: the window and the layer are filtered in the browser from the 30 days fetched.


data = _data()
notice = st.session_state.pop(NOTICE_KEY, None)  # shown for this run only
_handle(console_component.render(data, height=900, notice=notice))
