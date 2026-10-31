"""icecore app shell: navigation to the six pages and the views-schema setting.

Runs as a Streamlit in Snowflake object. Pages read the icecore views in the schema chosen
in the sidebar (default <current database>.ICECORE_OBS), never the raw event table.
"""
import streamlit as st

from lib import settings
from lib.navigation import PAGES

st.set_page_config(page_title="icecore", page_icon=":material/monitoring:", layout="wide")

settings.render_sidebar()

pages = [
    st.Page(page["path"], title=page["title"], icon=page["icon"], default=(i == 0))
    for i, page in enumerate(PAGES)
]
st.navigation(pages).run()
