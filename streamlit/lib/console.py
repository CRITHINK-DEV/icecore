"""The Console's page anatomy: title, KPI tiles, filter bar, table with a row click that opens the node.

This is the only module that touches Streamlit widgets for the anatomy. Every page is thin: it reads through
lib.queries (inside `guarded`), builds tiles and rows with pure functions, and calls these helpers.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st

from lib import settings
from lib.navigation import NODE_PAGE
from lib.status import COLOURS, STATUSES, label, normalise, sort_key  # noqa: F401  (COLOURS re-exported for the pages)

WINDOWS = (7, 15)
DEFAULT_DAYS = 15
NODE_STATE_KEY = "icecore_console_node"

_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|~$>:=])")


# --- escaping ---------------------------------------------------------------------------------------------------

def esc(s: Any) -> str:
    """Text for st.html: HTML-escaped. None is empty."""
    return "" if s is None else html.escape(str(s), quote=True)


def md(s: Any) -> str:
    """Text for st.markdown: markdown specials backslash-escaped and `<` made an entity, so names render as text."""
    if s is None:
        return ""
    text = str(s).replace("&", "&amp;").replace("<", "&lt;")
    # a newline would start a new markdown block (list, heading, quote): keep one line, as the names are labels
    text = re.sub(r"\s*[\r\n]+\s*", " ", text)
    return _MD_SPECIALS.sub(r"\\\1", text)


# --- header and states ------------------------------------------------------------------------------------------

def page(title: str, question: str, views: list[str]) -> None:
    """Title, then the question and "Reads N views"; the qualified view names sit in a help tooltip, not inline."""
    st.title(title)
    names = ", ".join(f"`{settings.view(v)}`" for v in views)
    count = f"{len(views)} view{'s' if len(views) != 1 else ''}"
    st.caption(f"{question} Reads {count}.", help=f"Views read by this page: {names}")


NO_DATA = "No data in this window"


def chart(spec: Any, rows: list | None) -> None:
    """Draw an Altair chart, or the caption "No data in this window" when it has no rows (never an empty axis)."""
    if not rows:
        st.caption(NO_DATA)
        return
    st.altair_chart(spec, use_container_width=True)


# --- table cell and title helpers -------------------------------------------------------------------------------

def column_title(name: str) -> str:
    """Title Case from snake_case: "last_success_at" -> "Last Success At", "status" -> "Status"."""
    return " ".join(w.capitalize() for w in str(name).split("_") if w)


def short_name(name: Any) -> Any:
    """The last part of a (possibly quoted) qualified relation name: '"DB"."SCHEMA"."NAME"' -> NAME."""
    if not isinstance(name, str) or not name:
        return name
    parts = re.findall(r'"([^"]*)"|([^.]+)', name)
    last = [a or b for a, b in parts]
    return last[-1].strip() if last else name


def short_id(value: Any, n: int = 8) -> Any:
    """The first `n` characters of a long id (an invocation_id)."""
    return value[:n] if isinstance(value, str) else value


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and v != v


def _display_rows(rows: list[dict]) -> list[dict]:
    """Rows as shown: None / NaN as a dash in text columns (blank in numeric and date ones), ids and relations shortened."""
    typed_cols = {k for r in rows for k, v in r.items() if v is not None and not _is_nan(v) and not isinstance(v, str)}
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            if k.endswith("invocation_id"):
                v = short_id(v)
            elif k == "relation":
                v = short_name(v)
            if v is None or _is_nan(v):
                v = None if k in typed_cols else "\u2014"
            d[k] = v
        out.append(d)
    return out


def column_config_with_titles(rows: list[dict], column_config: dict | None) -> dict:
    """The page's column_config plus a Title Case title for every column it does not mention. A None entry hides the
    column and a str entry is already a title."""
    config = dict(column_config or {})
    for key in {k for r in rows for k in r}:
        if key not in config:
            config[key] = st.column_config.TextColumn(column_title(key)) \
                if any(isinstance(r.get(key), str) for r in rows) else column_title(key)
    return config


def guarded(read: Callable[[], list[dict]]) -> tuple[list[dict], str | None]:
    """Run a read. On any exception return ([], the error text) so the page can show its empty state."""
    try:
        return list(read()), None
    except Exception as exc:  # no Snowpark session (offline) or the views are not created yet
        return [], f"{type(exc).__name__}: {exc}"


def empty(message: str, error: str | None = None, *, stop: bool = True) -> None:
    """The empty state: the message, the read error as a caption when there is one, then stop the page."""
    st.info(message, icon=":material/info:")
    if error:
        st.caption(f"Reading the views failed: {error}")
    if stop:
        st.stop()


# --- KPI tiles --------------------------------------------------------------------------------------------------

@dataclass
class Tile:
    label: str
    value: Any
    delta: str | None = None
    delta_color: str = "off"  # deltas are informational ("13 pending"): never a green or red arrow
    help: str = ""
    series: list[float] | None = None
    chart: str = "line"


def kpi_row(tiles: list[Tile]) -> None:
    if not tiles:
        return
    for col, tile in zip(st.columns(len(tiles)), tiles):
        with col:
            kwargs: dict[str, Any] = {"help": tile.help or None, "border": True}
            if tile.delta is not None:
                kwargs["delta"] = tile.delta
                kwargs["delta_color"] = tile.delta_color
            if tile.series and len(tile.series) > 1:
                kwargs["chart_data"] = list(tile.series)
                kwargs["chart_type"] = "bar" if tile.chart == "bar" else "line"
            st.metric(tile.label, "—" if tile.value is None else tile.value, **kwargs)


# --- filter bar -------------------------------------------------------------------------------------------------

@dataclass
class Filters:
    days: int
    layer: str | None
    statuses: list[str]


def _param(name: str) -> str | None:
    value = st.query_params.get(name)
    return value if isinstance(value, str) and value else None


def _mirror(name: str, value: str | None) -> None:
    if value:
        if st.query_params.get(name) != value:
            st.query_params[name] = value
    elif name in st.query_params:
        del st.query_params[name]


def filter_bar(key: str, *, layers: list[str] | None, statuses: bool = True) -> Filters:
    """Window (7 or 15 days), layer (hidden when `layers` is None) and status. Values mirror in st.query_params.

    Single choices use a horizontal st.radio, not st.segmented_control: AppTest (1.50) cannot re-run a page that holds a
    single-select button group (TypeError in ButtonGroup.indices), which would break every page test."""
    wanted = _param("days")
    default_days = int(wanted) if wanted and wanted.isdigit() and int(wanted) in WINDOWS else DEFAULT_DAYS
    cols = st.columns([1, 2, 3]) if (layers and statuses) else st.columns([1, 3])
    it = iter(cols)
    with next(it):
        days = st.radio("Window", list(WINDOWS), index=list(WINDOWS).index(default_days), key=f"{key}_days",
                        format_func=lambda d: f"{d} days", horizontal=True)
    layer = None
    if layers:
        options = ["All"] + list(layers)
        with next(it):
            wanted_layer = _param("layer")
            chosen = st.radio("Layer", options, index=options.index(wanted_layer) if wanted_layer in options else 0,
                              key=f"{key}_layer", horizontal=True)
        layer = None if chosen == "All" else chosen
    picked: list[str] = []
    if statuses:
        wanted_status = [s for s in (_param("status") or "").split(",") if s in STATUSES]
        with next(it):
            picked = list(st.pills("Status", list(STATUSES), selection_mode="multi", default=wanted_status or None,
                                   key=f"{key}_status", format_func=label) or [])
    _mirror("days", str(days))
    _mirror("layer", layer)
    _mirror("status", ",".join(picked))
    return Filters(days=int(days), layer=layer, statuses=picked)


# --- the table --------------------------------------------------------------------------------------------------

def truncation_note(rows: list, limit: int | None) -> str | None:
    """A caption when a read hit its limit, so a partial table is never mistaken for the whole."""
    if limit and len(rows) == limit:
        return f"Showing the latest {limit:,} rows."
    return None


def open_node(unique_id: str) -> None:
    """Open the node page for `unique_id`. The id travels in session state: st.switch_page (1.50) always clears the
    query parameters, so a ?node= set here would be lost. The node page calls `sync_node_param()` to put it back in
    the URL, which makes the view shareable."""
    st.session_state[NODE_STATE_KEY] = unique_id
    st.switch_page(NODE_PAGE)


def selected_node() -> str | None:
    """The node id the node page should show: the query string first, then the last one opened."""
    return _param("node") or st.session_state.get(NODE_STATE_KEY)


def sync_node_param() -> str | None:
    """For the node page: write the selected node into st.query_params["node"] so the link is shareable."""
    node = selected_node()
    if node and st.query_params.get("node") != node:
        st.query_params["node"] = node
    return node


def table(rows: list[dict], column_config: dict, key: str, *, limit: int | None = None,
          node_column: str | None = "unique_id") -> dict | None:
    """Failures first, a truncation caption, and on a single-row selection open the node. Returns the selected row."""
    ordered = sorted(rows, key=lambda r: sort_key(r["status"]) if "status" in r else len(STATUSES))
    shown = [{**r, "status": label(r["status"])} if "status" in r and not _already_label(r["status"]) else r
             for r in ordered]
    note = truncation_note(rows, limit)
    if not shown:
        st.caption("Nothing to show.")
        return None
    event = st.dataframe(_display_rows(shown), column_config=column_config_with_titles(shown, column_config), hide_index=True, width="stretch", key=key,
                         on_select="rerun", selection_mode="single-row")
    if note:
        st.caption(note)
    picked = list(event.selection.rows) if event is not None else []
    if not picked:
        return None
    row = ordered[picked[0]]
    if node_column and row.get(node_column):
        open_node(str(row[node_column]))
    return row


def _already_label(value: Any) -> bool:
    return isinstance(value, str) and value in {label(s) for s in COLOURS}
