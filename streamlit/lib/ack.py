"""The app's only write: acknowledge an incident of ALERTS_FEED.

The write path is the ICECORE_ACK procedure in the views schema (macros/setup/procedures.sql, the same procedure
`dbt run-operation icecore.acknowledge` calls). The procedure refuses a key that is not a current unacknowledged
incident and records the caller's own user (CURRENT_USER) as icecore.ack.by, so this module passes the incident key and
nothing else. The key travels as a bound parameter (`CALL <schema>."ICECORE_ACK"(?)`) through the query layer's session;
the only text that is not bound is the procedure's qualified name, validated as plain identifiers like every view name.

`acknowledge()` never raises: it returns an AckResult whose `kind` says what happened and whose `message` is the text to
show. It imports nothing from Streamlit, so it is unit-tested with a fake session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lib import queries

PROCEDURE = "ICECORE_ACK"
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_PRIVILEGE_MARKERS = ("insufficient privileges", "not authorized", "access control", "does not have usage",
                      "permission denied",
                      "unknown user-defined function")  # Snowflake's text for a procedure the role cannot see
_PRIVILEGE_ERRNOS = (2003, 3001, 2043)

# kinds: ok, already, refused, no_privilege, invalid, error
OK, ALREADY, REFUSED, NO_PRIVILEGE, INVALID, ERROR = "ok", "already", "refused", "no_privilege", "invalid", "error"


@dataclass(frozen=True)
class AckResult:
    kind: str
    message: str

    @property
    def ok(self) -> bool:
        return self.kind == OK


def procedure_name(views_schema: str) -> str:
    """`SCHEMA."ICECORE_ACK"` or `DATABASE.SCHEMA."ICECORE_ACK"`; the schema must be plain identifiers."""
    schema = (views_schema or "").strip()
    if not queries._SCHEMA_RE.match(schema):
        raise ValueError(f"not a schema name (SCHEMA or DATABASE.SCHEMA, unquoted): {views_schema!r}")
    return f'{schema.upper()}."{PROCEDURE}"'


def build_call(views_schema: str, incident_key: str) -> tuple[str, list[Any]]:
    """(sql, params) of the one write: the key is a bound parameter."""
    return f"call {procedure_name(views_schema)}(?)", [incident_key]


def _first_value(rows: list) -> str:
    if not rows:
        return ""
    row = rows[0]
    data = row.as_dict() if hasattr(row, "as_dict") else row
    if isinstance(data, dict):
        data = next(iter(data.values()), "")
    elif isinstance(data, (list, tuple)):
        data = data[0] if data else ""
    return str(data)


def _is_privilege_error(exc: Exception) -> bool:
    text = str(exc).lower()
    errno = getattr(exc, "errno", None) or getattr(exc, "sql_error_code", None)
    return errno in _PRIVILEGE_ERRNOS or any(m in text for m in _PRIVILEGE_MARKERS)


def _already_acknowledged(layer: queries.QueryLayer, incident_key: str) -> bool | None:
    """True when ALERTS_FEED shows this key acknowledged, False when it does not, None when that cannot be read."""
    try:
        sql = f"select acknowledged_at from {queries.qualified(layer.views_schema, 'ALERTS_FEED')} where incident_key = ?"
        rows = layer.session.sql(sql, params=[incident_key]).collect()
    except Exception:
        return None
    if not rows:
        return False
    row = rows[0]
    data = row.as_dict() if hasattr(row, "as_dict") else row
    return next(iter(data.values()), None) is not None


def acknowledge(incident_key: str, layer: queries.QueryLayer | None = None) -> AckResult:
    """Acknowledge one incident as the session's user. Clears the query cache when it lands."""
    key = (incident_key or "").strip() if isinstance(incident_key, str) else ""
    if not _KEY_RE.match(key):
        return AckResult(INVALID, "This is not an incident key (32 hex characters, a value of ALERTS_FEED.incident_key).")
    try:
        layer = layer or queries.default_layer()
        sql, params = build_call(layer.views_schema, key)
        status = _first_value(layer.session.sql(sql, params=params).collect())
    except Exception as exc:
        if _is_privilege_error(exc):
            return AckResult(NO_PRIVILEGE, (
                f"Your role cannot call {PROCEDURE}, so it cannot acknowledge incidents. It needs USAGE on the views "
                f"schema and on the procedure (Snowflake also reports this when setup has not been run in that "
                f"schema). Ask an administrator to run GRANT USAGE ON PROCEDURE <views schema>.{PROCEDURE}(STRING) "
                "TO ROLE <your role>."))
        return AckResult(ERROR, f"The acknowledgement was not recorded: {exc}")
    if status.startswith("ok"):
        queries.clear_cache(layer)
        return AckResult(OK, "Incident acknowledged. ALERTS_FEED now shows when and by whom.")
    if "not_an_open_incident" in status:
        if _already_acknowledged(layer, key):
            return AckResult(ALREADY, "This incident is already acknowledged.")
        return AckResult(REFUSED, status.removeprefix("icecore-error").strip())
    return AckResult(ERROR, status.removeprefix("icecore-error").strip() or "The acknowledgement was not recorded.")
