"""Reading the sandbox event table, which fills asynchronously."""
import datetime as dt
import json
import os
import time

from snowflake.connector import DictCursor

EVENT_TABLE = os.environ.get("ICECORE_EVENT_TABLE", "ICECORE_SANDBOX.TELEMETRY.EVENTS")

VARIANT_COLUMNS = {"resource_attributes", "scope", "scope_attributes", "record", "record_attributes", "trace", "value"}


def _decode(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        key = key.lower()
        if key in VARIANT_COLUMNS and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        out[key] = value
    return out


def build_query(where_sql: str, params: dict, since: dt.datetime | None) -> tuple[str, dict]:
    sql = f"select * from {EVENT_TABLE} where ({where_sql})"
    params = dict(params)
    if since is not None:
        # bound the scan of the shared event table; TIMESTAMP is when the event happened
        sql += " and TIMESTAMP >= %(_since)s"
        params["_since"] = since
    return sql, params


def _fetch(sf, sql: str, params: dict) -> list:
    cur = sf.cursor(DictCursor)
    try:
        return cur.execute(sql, params).fetchall()
    finally:
        cur.close()


def wait_for_events(sf, where_sql: str, params: dict, min_rows: int = 1, timeout_s: int = 300, poll_s: int = 5,
                    since: dt.datetime | None = None, settle_s: float = 0) -> list[dict]:
    """Poll until at least min_rows match; with settle_s, wait that long and re-read so exact counts do not race ingestion."""
    sql, params = build_query(where_sql, params, since)
    deadline = time.monotonic() + timeout_s
    while True:
        rows = _fetch(sf, sql, params)
        if len(rows) >= min_rows:
            if settle_s:
                time.sleep(settle_s)
                rows = _fetch(sf, sql, params)
            return [_decode(r) for r in rows]
        if time.monotonic() >= deadline:
            raise TimeoutError(f"saw {len(rows)} of {min_rows} rows after {timeout_s}s for: {where_sql}")
        time.sleep(poll_s)


def assert_no_events(sf, where_sql: str, params: dict, wait_s: float = 60, poll_s: int = 5, since: dt.datetime | None = None) -> None:
    """Fail if any matching row lands within wait_s (absence can only be shown by waiting out ingestion lag)."""
    sql, params = build_query(where_sql, params, since)
    deadline = time.monotonic() + wait_s
    while True:
        rows = _fetch(sf, sql, params)
        assert not rows, f"expected no rows, saw {len(rows)} for: {where_sql}"
        if time.monotonic() >= deadline:
            return
        time.sleep(poll_s)
