"""One status vocabulary for the whole app. Pure, no Streamlit."""
from __future__ import annotations

STATUSES = ("failed", "warning", "ok", "late", "not_run")

COLOURS: dict[str, str] = {
    "failed": "#c62828",
    "warning": "#b26a00",
    "ok": "#2e7d32",
    "late": "#6a4fb3",
    "not_run": "#9e9e9e",
}

_LABELS = {"failed": "🔴 Failed", "warning": "🟠 Warning", "ok": "🟢 OK", "late": "🟣 Late", "not_run": "⚪ Not run"}

_FROM_LABEL = {v: k for k, v in _LABELS.items()}

_MAP = {
    "success": "ok", "pass": "ok", "passed": "ok", "ok": "ok",
    "error": "failed", "fail": "failed", "failed": "failed", "runtime error": "failed",
    "warn": "warning", "warning": "warning",
    "skipped": "not_run", "not_run": "not_run", "not run": "not_run",
    "late": "late", "stale": "late", "silent": "late",
}


def normalise(raw: str | None) -> str:
    """Map a dbt or icecore status, or a label from `label()`, to one of STATUSES. Unknown or missing: `not_run`."""
    if isinstance(raw, str) and raw in _FROM_LABEL:
        return _FROM_LABEL[raw]
    return _MAP.get(str(raw).strip().lower(), "not_run") if raw is not None else "not_run"


def label(status: str) -> str:
    """Emoji-prefixed text for a table cell, for example "🔴 Failed". Accepts raw or normalised statuses."""
    return _LABELS[normalise(status)]


def sort_key(status: str) -> int:
    """Failed first, then warning, late, ok, not run."""
    return {"failed": 0, "warning": 1, "late": 2, "ok": 3, "not_run": 4}[normalise(status)]
