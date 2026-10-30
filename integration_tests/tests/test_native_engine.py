"""UC3 native engine: median and MAD bands, flat or seasonal (spec section 6).

Every series is fixed and passed as VALUES, so each verdict can be checked by hand.
"""
import statistics
from datetime import datetime, timedelta

import pytest

from helpers.macros import call_macro

END = datetime(2026, 10, 29, 6, 0)  # a Thursday


def series_sql(points: list[tuple[datetime, float]]) -> str:
    rows = ", ".join(f"('{t.isoformat(sep=' ')}', {v})" for t, v in points)
    return f"select column1::timestamp_ntz as observed_at, column2::float as value from values {rows}"


def daily(values: list[float], end: datetime = END) -> list[tuple[datetime, float]]:
    start = end - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def native(points, current_value, observed_at=None, sensitivity="medium", min_consecutive=1) -> dict:
    return call_macro(
        "native__score", series_sql=series_sql(points), current_value=current_value,
        sensitivity=sensitivity, min_consecutive=min_consecutive,
        observed_at=observed_at.isoformat(sep=" ") if observed_at else None,
    )


def mad(values: list[float]) -> float:
    m = statistics.median(values)
    return statistics.median(abs(v - m) for v in values)


NOISY = [100, 102, 98, 101, 99, 100, 103, 97, 100, 101, 99, 102, 98, 100, 101, 99, 100, 102, 98, 100]


# --- rendering (offline) ---------------------------------------------------------------------------


def test_native_renders_one_select_statement():
    sql = call_macro("native__score_sql", series_sql="select 1 as observed_at, 1 as value", current_value=1.5,
                     sensitivity="high", min_consecutive=2, observed_at="2026-10-29 06:00:00")
    lowered = sql.lower()
    assert ";" not in sql
    assert lowered.strip().startswith("with")
    assert "median(" in lowered
    assert "2 * " in lowered  # high sensitivity: 2 MAD


def test_observed_at_is_escaped():
    assert call_macro("_sql_timestamp", value="2026-10-29' or '1'='1") == "'2026-10-29'' or ''1''=''1'::timestamp_ntz"
    assert call_macro("_sql_timestamp", value=None) == "current_timestamp()::timestamp_ntz"


def test_current_value_must_be_a_number():
    from helpers.macros import MacroFailed

    with pytest.raises(MacroFailed, match="expected a number"):
        call_macro("native__score_sql", series_sql="select 1", current_value="1; drop table x",
                   sensitivity="medium", min_consecutive=1)


# --- flat window ---------------------------------------------------------------------------------------


@pytest.mark.sandbox
def test_flat_series_flags_the_500_point_and_not_the_others():
    values = [100.0] * 20
    values[12] = 500.0
    points = daily(values)
    for i in (12, 0, 11, 13, 19):
        history = points[:i] + points[i + 1:]
        result = native(history, values[i])
        assert result["is_anomaly"] is (i == 12), (i, result)
        assert result["baseline"] == "flat"
    outlier = native(points[:12] + points[13:], 500.0)
    assert outlier["expected"] == 100.0 and outlier["score"] == 400.0


@pytest.mark.sandbox
def test_band_is_median_plus_minus_k_mad_per_sensitivity():
    m, d = statistics.median(NOISY), mad(NOISY)
    for sensitivity, k in (("low", 4), ("medium", 3), ("high", 2)):
        result = native(daily(NOISY), 100, sensitivity=sensitivity)
        assert result["engine"] == "native"
        assert result["expected"] == pytest.approx(m)
        assert result["lower"] == pytest.approx(m - k * d)
        assert result["upper"] == pytest.approx(m + k * d)
        assert result["history_points"] == len(NOISY)


@pytest.mark.sandbox
def test_value_inside_the_band_scores_zero_and_outside_is_signed():
    inside = native(daily(NOISY), 101)
    assert inside["score"] == 0 and inside["is_anomaly"] is False
    above = native(daily(NOISY), 120)
    assert above["score"] == pytest.approx(120 - above["upper"]) and above["score"] > 0
    below = native(daily(NOISY), 80)
    assert below["score"] == pytest.approx(80 - below["lower"]) and below["score"] < 0
    assert below["is_anomaly"] is True


@pytest.mark.sandbox
def test_one_past_outlier_does_not_widen_the_band():
    clean = native(daily(NOISY), 100)
    with_outlier_values = NOISY[:10] + [10000] + NOISY[11:]
    dirty = native(daily(with_outlier_values), 100)
    mean_sd_upper = statistics.mean(with_outlier_values) + 3 * statistics.pstdev(with_outlier_values)
    assert dirty["upper"] - dirty["lower"] <= (clean["upper"] - clean["lower"]) * 1.5
    assert dirty["upper"] < 110 < mean_sd_upper  # mean + 3 sd would be above 6000
    assert native(daily(with_outlier_values), 115)["is_anomaly"] is True


@pytest.mark.sandbox
def test_fewer_than_seven_points_never_flags():
    result = native(daily([100, 101, 99, 100, 100]), 10000)
    assert result["is_anomaly"] is False and result["score"] == 0
    assert result["baseline"] == "insufficient_history"


# --- min_consecutive ---------------------------------------------------------------------------------


@pytest.mark.sandbox
def test_min_consecutive_needs_previous_points_out_of_band():
    two_high = daily(NOISY + [150, 150])
    assert native(two_high, 150, min_consecutive=3)["is_anomaly"] is True
    one_high = daily(NOISY + [100, 150])
    result = native(one_high, 150, min_consecutive=3)
    assert result["score"] > 0 and result["is_anomaly"] is False
    assert native(one_high, 150, min_consecutive=2)["is_anomaly"] is True


# --- seasonal buckets ----------------------------------------------------------------------------------


def hourly(weeks: float, end: datetime = datetime(2026, 10, 29, 23, 0)) -> list[tuple[datetime, float]]:
    """Business hours (9:00-17:00) run at ~150, nights at ~100, with a small fixed wobble."""
    hours = int(weeks * 7 * 24)
    start = end - timedelta(hours=hours - 1)
    points = []
    for i in range(hours):
        t = start + timedelta(hours=i)
        base = 150 if 9 <= t.hour <= 17 else 100
        points.append((t, base + (i % 5) - 2))
    return points


@pytest.mark.sandbox
def test_hourly_data_over_four_weeks_follows_the_hour_of_day_pattern():
    points = hourly(5)
    night = native(points, 100, observed_at=datetime(2026, 10, 30, 3, 0))
    day = native(points, 150, observed_at=datetime(2026, 10, 30, 12, 0))
    assert night["baseline"] == day["baseline"] == "seasonal"
    assert night["expected"] == pytest.approx(100, abs=3)
    assert day["expected"] == pytest.approx(150, abs=3)
    assert night["is_anomaly"] is False and day["is_anomaly"] is False
    # A business-hours value at night is an anomaly once the baseline is seasonal.
    assert native(points, 150, observed_at=datetime(2026, 10, 30, 3, 0))["is_anomaly"] is True


@pytest.mark.sandbox
def test_four_weeks_or_less_uses_the_flat_window():
    result = native(hourly(3), 100, observed_at=datetime(2026, 10, 30, 3, 0))
    assert result["baseline"] == "flat"


@pytest.mark.sandbox
def test_daily_data_over_four_weeks_follows_the_day_of_week_pattern():
    end = datetime(2026, 10, 29, 6, 0)
    points = [(end - timedelta(days=d), 100 + (d % 3) if (end - timedelta(days=d)).weekday() >= 5 else 1000 + (d % 3))
              for d in range(42)]
    saturday = datetime(2026, 10, 31, 6, 0)
    weekend = native(points, 100, observed_at=saturday)
    assert weekend["baseline"] == "seasonal"
    assert weekend["expected"] == pytest.approx(100, abs=3) and weekend["is_anomaly"] is False
    assert native(points, 1000, observed_at=saturday)["is_anomaly"] is True
