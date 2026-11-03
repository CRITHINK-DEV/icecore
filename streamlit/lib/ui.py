"""Shared page furniture. The anatomy lives in lib.console; these names stay for the pages not yet rebuilt."""
import streamlit as st

from lib import console


def page_header(title: str, purpose: str, views: list[str]) -> None:
    console.page(title, purpose, views)


def empty_state(message: str) -> None:
    st.info(message, icon=":material/info:")
