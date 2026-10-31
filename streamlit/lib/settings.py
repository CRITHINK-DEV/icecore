"""The views-schema setting: where every page reads the icecore views from.

The value lives in st.session_state for the browser session. Only plain Snowflake
identifiers (SCHEMA or DATABASE.SCHEMA, unquoted) are accepted, so the name can be
pasted into a query safely; they are upper-cased like Snowflake does.
"""
import re

import streamlit as st

DEFAULT_SCHEMA_NAME = "ICECORE_OBS"
STATE_KEY = "icecore_views_schema"
INPUT_KEY = "icecore_views_schema_input"
ERROR_KEY = "icecore_views_schema_error"

_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_SCHEMA_RE = re.compile(rf"^{_IDENT}(\.{_IDENT})?$")


def _current_database() -> str | None:
    """The active Snowpark session's database, or None outside Snowflake."""
    try:
        from snowflake.snowpark.context import get_active_session

        return get_active_session().get_current_database()
    except Exception:
        return None


def default_views_schema() -> str:
    database = (_current_database() or "").strip('"')
    if database and re.fullmatch(_IDENT, database):
        return f"{database.upper()}.{DEFAULT_SCHEMA_NAME}"
    return DEFAULT_SCHEMA_NAME


def normalize(value: str) -> str | None:
    """A valid schema name, upper-cased, or None."""
    value = (value or "").strip()
    return value.upper() if _SCHEMA_RE.match(value) else None


def views_schema() -> str:
    if STATE_KEY not in st.session_state:
        st.session_state[STATE_KEY] = default_views_schema()
    return st.session_state[STATE_KEY]


def view(name: str) -> str:
    """Fully qualified name of an icecore view in the chosen schema."""
    return f"{views_schema()}.{name.upper()}"


def _on_change() -> None:
    value = normalize(st.session_state[INPUT_KEY])
    if value is None:
        st.session_state[ERROR_KEY] = st.session_state[INPUT_KEY]
    else:
        st.session_state[STATE_KEY] = value
        st.session_state.pop(ERROR_KEY, None)


def render_sidebar() -> None:
    current = views_schema()
    if INPUT_KEY not in st.session_state:
        st.session_state[INPUT_KEY] = current
    st.sidebar.text_input(
        "Views schema",
        key=INPUT_KEY,
        on_change=_on_change,
        help="Schema holding the icecore views, created by `dbt run-operation icecore.setup`. "
        "SCHEMA or DATABASE.SCHEMA.",
    )
    if ERROR_KEY in st.session_state:
        st.sidebar.error(
            f"`{st.session_state[ERROR_KEY]}` is not a schema name (SCHEMA or DATABASE.SCHEMA, "
            f"unquoted). Still reading from `{current}`."
        )
