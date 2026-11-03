"""Settings page logic, pure Python (no Streamlit, no Snowflake).

Inputs are the lower-case row dicts of CAPABILITIES, INVOCATIONS, SLA_DECLARED and SILENCE_EVENTS.

Known gaps, shown on the page instead of invented:
- INVOCATIONS has `dbt_version` but no icecore version column; `icecore.version` is recorded on every record but
  only CAPABILITIES (the latest setup/doctor detection) exposes it.
- No view exposes the `icecore.notified` records, so a channel's last notification time cannot be read from the
  app (it never reads the event table). Channels are listed from SLA_DECLARED and SILENCE_EVENTS.
"""
from __future__ import annotations

NOT_RECORDED = "not recorded yet"
ICECORE_VERSION_NOTE = (
    "INVOCATIONS carries dbt's version per run but not icecore's yet (story ee1ee2c5). The icecore version in "
    "the capabilities is the one recorded by the latest setup or doctor run."
)
CHANNEL_NOTE = (
    "Channels are those named in `meta.alerts.channel` (SLA_DECLARED) or used by silence verdicts. The last "
    "notification time needs the NOTIFICATIONS view (story ee1ee2c5), so it reads \"not recorded yet\" until then."
)
EMPTY_MESSAGE = (
    "No capabilities recorded yet. Run `dbt run-operation icecore.setup` (or `icecore.doctor`) so icecore detects "
    "the edition, the event table and Cortex."
)


def _text(value, fallback="unknown"):
    return fallback if value is None or value == "" else str(value)


def capability_rows(cap: dict) -> list[tuple[str, str]]:
    """Label and value pairs for the latest CAPABILITIES detection."""
    edition = _text(cap.get("edition"))
    if cap.get("edition") and cap.get("edition_source"):
        edition = f"{edition} (from {cap['edition_source']})"
    level = cap.get("event_table_level")
    table = _text(cap.get("event_table"))
    if cap.get("event_table") and level:
        table = f"{table} ({level} level)"
    cortex = cap.get("cortex_available")
    if cortex is None:
        cortex_text = "unknown"
    elif cortex:
        cortex_text = "available"
    else:
        cortex_text = "not available" + (f": {cap['cortex_reason']}" if cap.get("cortex_reason") else "")
    return [
        ("Edition", edition),
        ("Event table", table),
        ("Cortex", cortex_text),
        ("Log level", _text(cap.get("log_level"))),
        ("Trace level", _text(cap.get("trace_level"))),
        ("icecore version (setup)", _text(cap.get("icecore_version"))),
        ("Detected at", _text(cap.get("detected_at"))),
    ]


def run_versions(runs: list[dict]) -> list[dict]:
    """One row per run with its dbt version and icecore version (NOT_RECORDED when the view has none)."""
    return [{
        "started_at": r.get("started_at"),
        "project": r.get("project"),
        "target": r.get("target"),
        "invocation_id": r.get("invocation_id"),
        "dbt": _text(r.get("dbt_version")),
        "icecore": _text(r.get("icecore_version"), NOT_RECORDED),
    } for r in runs]


def channel_rows(declared: list[dict], silences: list[dict], notified: list[dict] | None = None) -> list[dict]:
    """One row per channel: models routed to it, latest silence verdict, last notification (when rows are given)."""
    channels: dict[str, dict] = {}

    def row(name):
        return channels.setdefault(name, {"channel": name, "models": 0, "last_silence_at": None,
                                          "last_notified_at": NOT_RECORDED if notified is None else None})

    for d in declared:
        if d.get("channel"):
            row(d["channel"])["models"] += 1
    for s in silences:
        if s.get("channel"):
            r = row(s["channel"])
            if s.get("detected_at") is not None and (r["last_silence_at"] is None or s["detected_at"] > r["last_silence_at"]):
                r["last_silence_at"] = s["detected_at"]
    for n in notified or []:
        if n.get("channel"):
            r = row(n["channel"])
            if n.get("notified_at") is not None and (r["last_notified_at"] is None or n["notified_at"] > r["last_notified_at"]):
                r["last_notified_at"] = n["notified_at"]
    return sorted(channels.values(), key=lambda r: r["channel"])
