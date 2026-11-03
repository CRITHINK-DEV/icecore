"""icecore app (the Console): navigation to the ten pages, the views-schema setting and a global node search.

Runs as a Streamlit in Snowflake object, or locally. Pages read the icecore views in the schema chosen in the
sidebar (default <current database>.ICECORE_OBS), never the raw event table.
"""
import streamlit as st

from lib import console, queries, settings
from lib.navigation import PAGES, SECTIONS

st.set_page_config(page_title="icecore", page_icon=":material/monitoring:", layout="wide")

settings.render_sidebar()


def _search_nodes() -> dict[str, str]:
    """node name -> unique_id, from the latest lineage. Empty when the read fails (the search then hides)."""
    rows, _ = console.guarded(lambda: queries.lineage_latest(limit=queries.MAX_LIMIT))
    ids = {r.get(k) for r in rows for k in ("parent_unique_id", "child_unique_id") if r.get(k)}
    return {uid.split(".")[-1] + f"  ({uid})": uid for uid in sorted(ids)}


def _on_search() -> None:
    chosen = st.session_state.get("icecore_node_search")
    if chosen:
        console.open_node(_search_nodes()[chosen])


_nodes = _search_nodes()
if _nodes:
    st.sidebar.selectbox("Find a node", list(_nodes), index=None, placeholder="Search models, sources, tests",
                         key="icecore_node_search", on_change=_on_search)

sections: dict[str, list] = {}
for i, page in enumerate(PAGES):
    sections.setdefault(SECTIONS[page["group"]], []).append(
        st.Page(page["path"], title=page["title"], icon=page["icon"], default=(i == 0)))
st.navigation(sections).run()
