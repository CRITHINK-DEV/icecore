"""Shared page furniture."""
import streamlit as st

from lib import settings


def page_header(title: str, purpose: str, views: list[str]) -> None:
    st.title(title)
    st.caption(f"{purpose} Reads {', '.join(f'`{settings.view(v)}`' for v in views)}.")


def empty_state(message: str) -> None:
    st.info(message, icon=":material/info:")
