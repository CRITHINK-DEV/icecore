"""Reading the sandbox event table, which fills asynchronously."""
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


def wait_for_events(sf, where_sql: str, params: dict, min_rows: int = 1, timeout_s: int = 300, poll_s: int = 5) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    rows: list = []
    while True:
        rows = sf.cursor(DictCursor).execute(f"select * from {EVENT_TABLE} where {where_sql}", params).fetchall()
        if len(rows) >= min_rows:
            return [_decode(r) for r in rows]
        if time.monotonic() >= deadline:
            raise TimeoutError(f"saw {len(rows)} of {min_rows} rows after {timeout_s}s for: {where_sql}")
        time.sleep(poll_s)
