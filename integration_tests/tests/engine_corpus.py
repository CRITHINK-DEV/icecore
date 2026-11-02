"""Fixed series for the engine corpus (test_engine_corpus.py). Pure Python, deterministic: no randomness.

Each case is (history points, current value, observed_at of the current value). Daily series end on END and the
current value is observed one day later; the seasonal series is hourly.
"""
from datetime import datetime, timedelta

END = datetime(2026, 10, 29, 6, 0)  # a Thursday
NOISY = [100, 102, 98, 101, 99, 100, 103, 97, 100, 101, 99, 102, 98, 100, 101, 99, 100, 102, 98, 100]


def _daily(values, end=END):
    start = end - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), float(v)) for i, v in enumerate(values)]


def _seasonal_hourly(weeks=6, end=datetime(2026, 10, 29, 23, 0)):
    """Business hours (9-17) run at ~150, other hours at ~100, with a small fixed wobble."""
    hours = weeks * 7 * 24
    start = end - timedelta(hours=hours - 1)
    out = []
    for i in range(hours):
        t = start + timedelta(hours=i)
        out.append((t, float((150 if 9 <= t.hour <= 17 else 100) + (i % 5) - 2)))
    return out


def _flat():
    return [100 + (i % 3) - 1 for i in range(30)]


CORPUS = {
    "flat": dict(points=_daily(_flat()), value=101.0, at=END + timedelta(days=1)),
    "seasonal": dict(points=_seasonal_hourly(), value=150.0, at=datetime(2026, 10, 30, 3, 0)),  # business level at 03:00
    "single_outlier": dict(points=_daily(_flat()), value=500.0, at=END + timedelta(days=1)),
    "step_change": dict(points=_daily([100] * 20 + [200] * 10), value=200.0, at=END + timedelta(days=1)),
    "trend": dict(points=_daily([100 + i for i in range(30)]), value=160.0, at=END + timedelta(days=1)),
    "sparse": dict(points=_daily([100, 101, 99, 100, 100]), value=10000.0, at=END + timedelta(days=1)),
    "holiday_dip": dict(points=_daily([100 if i != 17 else 40 for i in range(30)]), value=40.0, at=END + timedelta(days=1)),
    "noisy": dict(points=_daily(NOISY + NOISY), value=101.0, at=END + timedelta(days=1)),
}


def values_sql(points):
    rows = ", ".join(f"('{t.isoformat(sep=' ')}', {v})" for t, v in points)
    return f"select column1::timestamp_ntz as observed_at, column2::float as value from values {rows}"
