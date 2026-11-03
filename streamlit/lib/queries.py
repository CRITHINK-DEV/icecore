"""The app's one query layer: every read the pages make, one typed function per icecore view.

Rules this module enforces (the story's acceptance criteria and the Streamlit in Snowflake skill):
- Pages call this module and nothing else for data. It reads the icecore views in the views schema, never the event
  table and never base_events (whose LIKE literals clash with placeholders).
- Every value is a bound parameter (`?`, Snowpark qmark style). The only text that is not bound is the view's qualified
  name, which is validated as plain identifiers (SCHEMA or DATABASE.SCHEMA) and a fixed view name, and the column and
  ORDER BY lists, which are constants in this file. No string formatting of values.
- Results are cached in the page session (st.session_state by default) for a short TTL, keyed on the SQL text and the
  parameters, so the same call twice runs one query. `clear_cache()` empties it (after an acknowledgement).
- It imports nothing from Streamlit at import time, so the builders and the cache are unit-tested with a fake session.

Each function returns a list of rows, one dict per row with lower-case keys (the column list of VIEWS[name]). The
`status` of each view in VIEWS says where its column list comes from: `merged` is on develop (macros/views/*.sql, checked against the rendered create_views by a test),
`pr` is written against an open pull request and `spec` against the spec only, to be checked when it merges. A view
that is not merged yet is coming: its function returns no rows and runs no query, so a page that calls it early shows
its empty state instead of failing on a view that does not exist.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, MutableMapping, TypedDict

DEFAULT_TTL_S = 60
CACHE_STATE_KEY = "icecore_query_cache"
DEFAULT_LIMIT = 1000
MAX_LIMIT = 50_000

_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_SCHEMA_RE = re.compile(rf"^{_IDENT}(\.{_IDENT})?$")


# --- the views and their columns --------------------------------------------------------------------------------

# name -> (status, source, columns). Columns are the view's output names in lower case; "COLUMN" is read as `column`.
VIEWS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "METRICS": ("merged", "macros/views/metrics.sql", (
        "relation", "column", "dimension", "metric", "observed_at", "value", "anomaly_engine", "anomaly_expected",
        "anomaly_lower", "anomaly_upper", "anomaly_score", "anomaly_is_anomaly", "invocation_id", "node_unique_id",
        "emit_query_id", "threshold")),
    "SCHEMA_CHANGES": ("merged", "macros/views/schema_changes.sql", (
        "relation", "change_kind", "column", "from_type", "to_type", "detected_at", "invocation_id",
        "node_unique_id", "emit_query_id")),
    "TEST_RESULTS": ("merged", "macros/views/test_results.sql", (
        "invocation_id", "project", "test_unique_id", "test_name", "test_type", "column", "severity", "status",
        "failures", "message", "execution_time_s", "root_cause_unique_id", "root_cause_kind", "engine", "expected", "lower", "upper", "score", "executed_at",
        "query_id")),
    "LINEAGE": ("merged", "macros/views/lineage.sql", (
        "invocation_id", "project", "parent_unique_id", "child_unique_id", "recorded_at", "parent_layer", "child_layer")),
    "LINEAGE_LATEST": ("merged", "macros/views/lineage.sql", (
        "invocation_id", "project", "parent_unique_id", "child_unique_id", "recorded_at", "parent_layer", "child_layer")),
    "CAPABILITIES": ("merged", "macros/views/capabilities.sql", (
        "database_name", "detected_at", "edition", "edition_source", "event_table", "event_table_level",
        "cortex_available", "cortex_reason", "log_level", "trace_level", "icecore_version", "invocation_id")),
    "INVOCATIONS": ("merged", "macros/views/invocations.sql", (
        "invocation_id", "runtime", "command", "target", "project", "dbt_version", "started_at", "ended_at",
        "duration_s", "status", "nodes_total", "nodes_success", "nodes_failed", "nodes_warned", "nodes_skipped",
        "credits")),
    "NODE_EXECUTIONS": ("merged", "macros/views/node_executions.sql", (
        "invocation_id", "unique_id", "type", "name", "materialization", "relation", "status", "message", "rows_affected",
        "execution_time_s", "started_at", "completed_at", "query_id", "query_ids", "credits", "credits_settled",
        "root_cause_unique_id",
        "recorded_at", "layer")),
    "ALERTS_FEED": ("merged", "macros/views/alerts_feed.sql", (
        "incident_key", "kind", "node_key", "window_start", "severity", "title", "first_detected_at",
        "last_detected_at", "occurrences", "last_invocation_id", "acknowledged_at", "acknowledged_by")),
    "SILENCE_EVENTS": ("merged", "macros/views/silence_events.sql", (
        "unique_id", "expected_at", "grace", "expected_every_minutes", "last_success_at", "detected_at", "channel",
        "severity")),
    "SLA_DECLARED": ("merged", "macros/control/create_sla_declared_view.sql", (
        "unique_id", "expected_every_minutes", "grace_minutes", "channel", "severity")),
}


class Row(TypedDict, total=False):
    """A result row: the columns of the view, lower case. Typed per view below."""


class MetricRow(TypedDict):
    relation: str
    column: str | None
    dimension: str | None
    metric: str
    observed_at: datetime
    value: float
    anomaly_engine: str | None
    anomaly_expected: float | None
    anomaly_lower: float | None
    anomaly_upper: float | None
    anomaly_score: float | None
    anomaly_is_anomaly: bool | None
    invocation_id: str | None
    node_unique_id: str | None
    emit_query_id: str | None


class SchemaChangeRow(TypedDict):
    relation: str
    change_kind: str
    column: str | None
    from_type: str | None
    to_type: str | None
    detected_at: datetime
    invocation_id: str | None
    node_unique_id: str | None
    emit_query_id: str | None


# --- builders: (sql, params), pure -------------------------------------------------------------------------------

def qualified(views_schema: str, view: str) -> str:
    """`SCHEMA.VIEW` or `DATABASE.SCHEMA.VIEW`; the schema must be plain identifiers, the view one of VIEWS."""
    schema = (views_schema or "").strip()
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"not a schema name (SCHEMA or DATABASE.SCHEMA, unquoted): {views_schema!r}")
    if view not in VIEWS:
        raise ValueError(f"unknown icecore view: {view!r}")
    return f"{schema.upper()}.{view}"


def _select_list(view: str, present: frozenset[str] | None = None) -> str:
    """The view's columns; with `present` (upper-case names the view really has), only those."""
    cols = []
    for name in VIEWS[view][2]:
        if present is not None and name.upper() not in present:
            continue
        cols.append('"COLUMN" as "column"' if name == "column" else name)
    return ", ".join(cols)


def _limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be a positive integer: {limit!r}")
    return min(limit, MAX_LIMIT)


def _ts(value: datetime | date | str) -> str:
    """A timestamp parameter as the ISO text that to_timestamp_ntz(?) takes."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class _Where:
    def __init__(self) -> None:
        self.clauses: list[str] = []
        self.params: list[Any] = []

    def eq(self, column: str, value: Any) -> None:
        if value is not None:
            self.clauses.append(f"{column} = ?")
            self.params.append(value)

    def in_(self, column: str, values: list[str] | tuple[str, ...] | None) -> None:
        if values:
            self.clauses.append(f"{column} in ({', '.join('?' for _ in values)})")
            self.params.extend(values)

    def at_least(self, column: str, value: Any) -> None:
        if value is not None:
            self.clauses.append(f"{column} >= to_timestamp_ntz(?)")
            self.params.append(_ts(value))

    def before(self, column: str, value: Any) -> None:
        if value is not None:
            self.clauses.append(f"{column} < to_timestamp_ntz(?)")
            self.params.append(_ts(value))

    def sql(self) -> str:
        return (" where " + " and ".join(self.clauses)) if self.clauses else ""


def _build(views_schema: str, view: str, where: _Where, order_by: str, limit: int) -> tuple[str, list[Any]]:
    sql = (f"select {_select_list(view)} from {qualified(views_schema, view)}{where.sql()} "
           f"order by {order_by} limit ?")
    return sql, [*where.params, _limit(limit)]


def build_metrics(views_schema, relation=None, metric=None, column=None, dimension=None, since=None, until=None,
                  limit=DEFAULT_LIMIT):
    w = _Where()
    w.eq("relation", relation)
    w.eq("metric", metric)
    w.eq('"COLUMN"', column)
    w.eq("dimension", dimension)
    w.at_least("observed_at", since)
    w.before("observed_at", until)
    return _build(views_schema, "METRICS", w, "observed_at desc", limit)


def build_schema_changes(views_schema, relation=None, change_kind=None, since=None, limit=DEFAULT_LIMIT):
    w = _Where()
    w.eq("relation", relation)
    w.eq("change_kind", change_kind)
    w.at_least("detected_at", since)
    return _build(views_schema, "SCHEMA_CHANGES", w, "detected_at desc", limit)


def build_test_results(views_schema, test_unique_id=None, statuses=None, invocation_id=None, since=None,
                       limit=DEFAULT_LIMIT):
    w = _Where()
    w.eq("test_unique_id", test_unique_id)
    w.in_("status", statuses)
    w.eq("invocation_id", invocation_id)
    w.at_least("executed_at", since)
    return _build(views_schema, "TEST_RESULTS", w, "executed_at desc", limit)


def build_lineage(views_schema, invocation_id=None, project=None, limit=MAX_LIMIT):
    w = _Where()
    w.eq("invocation_id", invocation_id)
    w.eq("project", project)
    return _build(views_schema, "LINEAGE", w, "recorded_at desc, parent_unique_id, child_unique_id", limit)


def build_lineage_latest(views_schema, project=None, limit=MAX_LIMIT):
    w = _Where()
    w.eq("project", project)
    return _build(views_schema, "LINEAGE_LATEST", w, "parent_unique_id, child_unique_id", limit)


def build_capabilities(views_schema, limit=1):
    return _build(views_schema, "CAPABILITIES", _Where(), "detected_at desc", limit)


def build_invocations(views_schema, statuses=None, since=None, limit=200):
    w = _Where()
    w.in_("status", statuses)
    w.at_least("started_at", since)
    return _build(views_schema, "INVOCATIONS", w, "started_at desc nulls last", limit)


def build_node_executions(views_schema, invocation_id=None, statuses=None, limit=DEFAULT_LIMIT, since=None):
    w = _Where()
    w.eq("invocation_id", invocation_id)
    w.in_("status", statuses)
    w.at_least("started_at", since)
    return _build(views_schema, "NODE_EXECUTIONS", w, "started_at nulls last, unique_id", limit)


def build_alerts_feed(views_schema, acknowledged=None, severity=None, kind=None, since=None, limit=500):
    w = _Where()
    if acknowledged is True:
        w.clauses.append("acknowledged_at is not null")
    elif acknowledged is False:
        w.clauses.append("acknowledged_at is null")
    w.eq("severity", severity)
    w.eq("kind", kind)
    w.at_least("last_detected_at", since)
    return _build(views_schema, "ALERTS_FEED", w, "last_detected_at desc", limit)


def build_silence_events(views_schema, unique_id=None, since=None, limit=DEFAULT_LIMIT):
    w = _Where()
    w.eq("unique_id", unique_id)
    w.at_least("detected_at", since)
    return _build(views_schema, "SILENCE_EVENTS", w, "detected_at desc", limit)


# --- execution and the page-session cache -----------------------------------------------------------------------

class QueryLayer:
    """A Snowpark-style session (`session.sql(sql, params=[...]).collect()`), the views schema, a cache mapping and a TTL."""

    def __init__(self, session: Any, views_schema: str, cache: MutableMapping | None = None,
                 ttl_s: float = DEFAULT_TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.session = session
        self.views_schema = views_schema
        self.cache: MutableMapping = {} if cache is None else cache
        self.ttl_s = ttl_s
        self.clock = clock

    def run(self, built: tuple[str, list[Any]]) -> list[dict]:
        sql, params = built
        key = (sql, tuple(params))
        now = self.clock()
        for stale in [k for k, (at, _) in self.cache.items() if now - at >= self.ttl_s]:
            del self.cache[stale]  # evict expired entries, so a long page session does not keep every old result
        hit = self.cache.get(key)
        if hit is not None and now - hit[0] < self.ttl_s:
            return [dict(r) for r in hit[1]]
        rows = [_as_row(r) for r in self.session.sql(sql, params=params).collect()]
        self.cache[key] = (now, rows)
        return [dict(r) for r in rows]

    def clear(self) -> None:
        self.cache.clear()


def _as_row(row: Any) -> dict:
    data = row.as_dict() if hasattr(row, "as_dict") else dict(row)
    return {str(k).lower(): v for k, v in data.items()}


# --- the session: Snowflake's own inside Streamlit in Snowflake, one built from the environment on a laptop ------

LOCAL_SESSION_KEY = "icecore_local_session"
_local: dict[str, Any] = {}  # one Snowpark session per app process (module state survives Streamlit reruns)
_local_lock = threading.Lock()


def session_params(env: MutableMapping | None = None) -> dict | None:
    """Connection parameters for a local run, chosen from the environment; None when nothing is configured.

    Order: SNOWFLAKE_CONNECTION_NAME (an entry of connections.toml, which the Snowflake library reads itself: this
    code never opens that file); else SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER with a key pair
    (SNOWFLAKE_PRIVATE_KEY_PATH, optional SNOWFLAKE_PRIVATE_KEY_PASSPHRASE) or, without a key, the browser login
    (externalbrowser). SNOWFLAKE_ROLE, _WAREHOUSE and _DATABASE are optional. Pure: no network, nothing printed.
    """
    import os

    env = os.environ if env is None else env

    def get(name: str) -> str | None:
        return (env.get(name) or "").strip() or None

    if get("SNOWFLAKE_CONNECTION_NAME"):
        params: dict = {"connection_name": get("SNOWFLAKE_CONNECTION_NAME")}
    elif get("SNOWFLAKE_ACCOUNT") and get("SNOWFLAKE_USER"):
        params = {"account": get("SNOWFLAKE_ACCOUNT"), "user": get("SNOWFLAKE_USER")}
        if get("SNOWFLAKE_PRIVATE_KEY_PATH"):
            params["private_key_file"] = get("SNOWFLAKE_PRIVATE_KEY_PATH")
            if get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
                params["private_key_file_pwd"] = get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
        else:
            params["authenticator"] = "externalbrowser"
    else:
        return None
    for key, name in (("role", "SNOWFLAKE_ROLE"), ("warehouse", "SNOWFLAKE_WAREHOUSE"), ("database", "SNOWFLAKE_DATABASE")):
        if get(name):
            params[key] = get(name)
    params["client_session_keep_alive"] = True  # the app stays open for hours; without it the session expires (~4 h idle)
    return params


def get_session(env: MutableMapping | None = None, store: dict | None = None) -> Any:
    """The active Snowpark session in Snowflake; on a laptop, one session built from the environment and kept.

    With no active session and no configuration, re-raises the lookup error: pages show their empty state.
    """
    from snowflake.snowpark import Session
    from snowflake.snowpark.context import get_active_session

    try:
        return get_active_session()
    except Exception:
        params = session_params(env)
        if params is None:
            raise
        store = _local if store is None else store
        with _local_lock:  # Streamlit reruns can overlap: build one session, not one per rerun
            if LOCAL_SESSION_KEY not in store:
                store[LOCAL_SESSION_KEY] = Session.builder.configs(params).create()
        return store[LOCAL_SESSION_KEY]


def default_layer() -> QueryLayer:
    """The layer of the running page: the session (see get_session), the chosen views schema, st.session_state."""
    import streamlit as st

    from lib import settings

    return QueryLayer(get_session(), settings.views_schema(),
                      cache=st.session_state.setdefault(CACHE_STATE_KEY, {}))


def clear_cache(layer: QueryLayer | None = None) -> None:
    (layer or default_layer()).clear()


def is_available(view: str) -> bool:
    """True when the view is merged, so the setup creates it; a `pr` or `spec` view is coming."""
    return VIEWS[view][0] == "merged"


# Columns a view really has, per (views schema, view), learned when a read hits a column the view does not have yet:
# a view created by an older icecore lacks the newest columns until `icecore.setup` runs again. Kept for the app
# process: after setup upgrades the views, restart the app to read the new columns.
_VIEW_COLUMNS: dict[tuple[str, str], frozenset[str]] = {}
_MISSING_COLUMN = "invalid identifier"


def _view_columns(layer: QueryLayer, view: str) -> frozenset[str]:
    rows = layer.session.sql(f"describe view {qualified(layer.views_schema, view)}").collect()
    return frozenset(str(_as_row(r).get("name", "")).upper() for r in rows)


def _only_present(built: tuple[str, list[Any]], view: str, present: frozenset[str]) -> tuple[str, list[Any]]:
    sql, params = built
    return sql.replace(_select_list(view), _select_list(view, present), 1), params


def _read(builder: Callable, layer: QueryLayer | None, *args, view: str, **kwargs) -> list[dict]:
    """Rows of one view. A view from an older icecore that lacks some of VIEWS' columns is read with the columns it
    has (looked up once with DESCRIBE VIEW); the missing keys are simply absent and pages show "not recorded yet"."""
    if not is_available(view):
        return []
    layer = layer or default_layer()
    built = builder(layer.views_schema, *args, **kwargs)
    key = (layer.views_schema.upper(), view)
    present = _VIEW_COLUMNS.get(key)
    if present is not None:
        return layer.run(_only_present(built, view, present))
    try:
        return layer.run(built)
    except Exception as exc:
        if _MISSING_COLUMN not in str(exc).lower():
            raise
        present = _VIEW_COLUMNS[key] = _view_columns(layer, view)
        return layer.run(_only_present(built, view, present))


# --- one function per view ---------------------------------------------------------------------------------------

def metrics(relation: str | None = None, metric: str | None = None, column: str | None = None,
            dimension: str | None = None, since=None, until=None, limit: int = DEFAULT_LIMIT,
            layer: QueryLayer | None = None) -> list[MetricRow]:
    """METRICS rows, newest first, filtered on whatever is given."""
    return _read(build_metrics, layer, relation, metric, column, dimension, since, until, limit, view="METRICS")


def schema_changes(relation: str | None = None, change_kind: str | None = None, since=None,
                   limit: int = DEFAULT_LIMIT, layer: QueryLayer | None = None) -> list[SchemaChangeRow]:
    """SCHEMA_CHANGES rows, newest first."""
    return _read(build_schema_changes, layer, relation, change_kind, since, limit, view="SCHEMA_CHANGES")


def test_results(test_unique_id: str | None = None, statuses: list[str] | None = None,
                 invocation_id: str | None = None, since=None, limit: int = DEFAULT_LIMIT,
                 layer: QueryLayer | None = None) -> list[Row]:
    """TEST_RESULTS rows, newest first."""
    return _read(build_test_results, layer, test_unique_id, statuses, invocation_id, since, limit, view="TEST_RESULTS")


def lineage(invocation_id: str | None = None, project: str | None = None, limit: int = MAX_LIMIT,
            layer: QueryLayer | None = None) -> list[Row]:
    """LINEAGE edges, one row per invocation, parent and child."""
    return _read(build_lineage, layer, invocation_id, project, limit, view="LINEAGE")


def lineage_latest(project: str | None = None, limit: int = MAX_LIMIT, layer: QueryLayer | None = None) -> list[Row]:
    """The edges of the latest invocation of each project."""
    return _read(build_lineage_latest, layer, project, limit, view="LINEAGE_LATEST")


def capabilities(limit: int = 1, layer: QueryLayer | None = None) -> list[Row]:
    """CAPABILITIES detections, latest first (default: the latest one)."""
    return _read(build_capabilities, layer, limit, view="CAPABILITIES")


def invocations(statuses: list[str] | None = None, since=None, limit: int = 200,
                layer: QueryLayer | None = None) -> list[Row]:
    """INVOCATIONS, newest first."""
    return _read(build_invocations, layer, statuses, since, limit, view="INVOCATIONS")


def node_executions(invocation_id: str | None = None, statuses: list[str] | None = None,
                    limit: int = DEFAULT_LIMIT, layer: QueryLayer | None = None, since=None) -> list[Row]:
    """NODE_EXECUTIONS of an invocation (or all), in start order; since filters on started_at."""
    return _read(build_node_executions, layer, invocation_id, statuses, limit, since, view="NODE_EXECUTIONS")


def alerts_feed(acknowledged: bool | None = None, severity: str | None = None, kind: str | None = None,
                since=None, limit: int = 500, layer: QueryLayer | None = None) -> list[Row]:
    """ALERTS_FEED incidents, latest first; acknowledged True, False or None (both). No rows until #57 is merged."""
    return _read(build_alerts_feed, layer, acknowledged, severity, kind, since, limit, view="ALERTS_FEED")


def silence_events(unique_id: str | None = None, since=None, limit: int = DEFAULT_LIMIT,
                   layer: QueryLayer | None = None) -> list[Row]:
    """SILENCE_EVENTS: silence verdicts written by the silence task (macros/views/silence_events.sql)."""
    return _read(build_silence_events, layer, unique_id, since, limit, view="SILENCE_EVENTS")


def build_sla_declared(views_schema, unique_id=None, limit=MAX_LIMIT):
    w = _Where()
    w.eq("unique_id", unique_id)
    return _build(views_schema, "SLA_DECLARED", w, "unique_id", limit)


def sla_declared(unique_id: str | None = None, limit: int = MAX_LIMIT, layer: QueryLayer | None = None) -> list[Row]:
    """SLA_DECLARED: every model with meta.sla (created by setup, macros/control/create_sla_declared_view.sql)."""
    return _read(build_sla_declared, layer, unique_id, limit, view="SLA_DECLARED")
