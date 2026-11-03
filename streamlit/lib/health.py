"""Health score: four components from 0 to 100, one weighted score. Pure, no Streamlit."""
from __future__ import annotations

from lib.status import normalise

WEIGHTS = {"tests": 0.5, "freshness": 0.2, "schedules": 0.15, "anomalies": 0.15}

_TEST_POINTS = {"ok": 100.0, "warning": 50.0, "failed": 0.0}


def _pct(flags: list[bool], good: bool) -> float | None:
    if not flags:
        return None
    return round(100.0 * sum(1 for f in flags if bool(f) is good) / len(flags), 1)


def components(test_statuses: list[str], sources_within_limit: list[bool], schedules_on_time: list[bool],
                metrics_anomalous: list[bool]) -> dict[str, float | None]:
    """Each component from 0 to 100, or None (n/a) when its list is empty. Skipped or unknown tests are left out."""
    points = [_TEST_POINTS[s] for s in (normalise(t) for t in test_statuses) if s in _TEST_POINTS]
    return {
        "tests": round(sum(points) / len(points), 1) if points else None,
        "freshness": _pct(sources_within_limit, True),
        "schedules": _pct(schedules_on_time, True),
        "anomalies": _pct(metrics_anomalous, False),
    }


def score(components: dict[str, float | None]) -> float | None:
    """Weighted mean over the components that apply (not None); None when none do."""
    used = {k: v for k, v in components.items() if v is not None and k in WEIGHTS}
    total = sum(WEIGHTS[k] for k in used)
    if not used or total == 0:
        return None
    return round(sum(WEIGHTS[k] * v for k, v in used.items()) / total, 1)


def formula_help() -> str:
    """Markdown for the score tile's `help`."""
    return (
        "Weighted mean of four components, each 0 to 100, left out when it does not apply:\n\n"
        f"- tests ({WEIGHTS['tests']}): pass 100, warn 50, fail or error 0\n"
        f"- freshness ({WEIGHTS['freshness']}): % of sources within their limit\n"
        f"- schedules ({WEIGHTS['schedules']}): % of declared schedules on time\n"
        f"- anomalies ({WEIGHTS['anomalies']}): 100 minus the % of monitored metrics that are anomalous\n\n"
        "The weights are renormalised over the components that apply."
    )
