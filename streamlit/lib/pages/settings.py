"""Settings & about: pure logic (rows in, tiles and table rows out). Builds on lib.config_logic."""
from __future__ import annotations

from lib import config_logic as cl
from lib.console import Tile
from lib.config_logic import (CHANNEL_NOTE, EMPTY_MESSAGE, ICECORE_VERSION_NOTE, NOT_RECORDED,  # noqa: F401
                              capability_rows, channel_rows, run_versions)


def latest_capabilities(rows: list[dict]) -> dict:
    """The newest detection (the view returns the latest first); {} when there is none."""
    dated = [r for r in rows if r.get("detected_at") is not None]
    return max(dated, key=lambda r: r["detected_at"]) if dated else (rows[0] if rows else {})


def tiles(cap: dict, channels: list[dict]) -> list[Tile]:
    cortex = cap.get("cortex_available")
    return [
        Tile("Edition", cap.get("edition") or "—"),
        Tile("icecore version", cap.get("icecore_version") or "—"),
        Tile("Cortex anomaly engine", "—" if cortex is None else ("Available" if cortex else "Not available")),
        Tile("Alert channels", len(channels)),
    ]


def version_rows(runs: list[dict], limit: int = 20) -> list[dict]:
    """The latest runs first, with dbt and icecore versions ("not recorded yet" for a missing icecore version)."""
    dated = sorted((r for r in runs if r.get("started_at") is not None), key=lambda r: r["started_at"], reverse=True)
    return cl.run_versions((dated + [r for r in runs if r.get("started_at") is None])[:limit])
