"""UC3 native engine: median and MAD bands, flat or seasonal (spec section 6).

Every series is fixed and passed as VALUES, so each verdict can be checked by hand.
"""
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from helpers.macros import call_macro

REPO = Path(__file__).resolve().parents[2]
END = datetime(2026, 10, 29, 6, 0)  # a Thursday
MAD_FLOOR_PCT = 0.01  # var anomaly_mad_floor_pct default


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


def effective_mad(values: list[float]) -> float:
    """MAD floored at anomaly_mad_floor_pct x |median| (anomaly_mad_floor_abs defaults to 0)."""
    return max(mad(values), MAD_FLOOR_PCT * abs(statistics.median(values)), 0)


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
    assert call_macro("_sql_timestamp", value="2026-10-29' or '1'='1") == "'2026-10-29\\' or \\'1\\'=\\'1'::timestamp_ntz"
    # History timestamps are UTC (event table); the default "now" must be UTC too, not the session time zone.
    assert call_macro("_sql_timestamp", value=None) == "convert_timezone('UTC', current_timestamp())::timestamp_ntz"


def test_package_declares_the_mad_floor_vars():
    cfg = yaml.safe_load(open(REPO / "dbt_project.yml"))["vars"]["icecore"]
    assert cfg["anomaly_mad_floor_pct"] == 0.01
    assert cfg["anomaly_mad_floor_abs"] == 0


def test_native_sql_applies_the_mad_floor():
    sql = call_macro("native__score_sql", series_sql="select 1 as observed_at, 1 as value", current_value=1,
                     sensitivity="medium", min_consecutive=1).lower()
    assert "greatest(" in sql and "0.01" in sql


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
    # MAD is 0, so the floor (1% of the median) sets the band to 100 ± 3.
    assert outlier["expected"] == 100.0 and outlier["upper"] == pytest.approx(103.0)
    assert outlier["score"] == pytest.approx(397.0)


@pytest.mark.sandbox
def test_band_is_median_plus_minus_k_mad_per_sensitivity():
    m, d = statistics.median(NOISY), effective_mad(NOISY)
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


@pytest.mark.sandbox
def test_mad_floor_keeps_a_constant_series_from_flagging_a_tiny_change():
    constant = daily([1000.0] * 20)
    small = native(constant, 1001)
    assert small["lower"] == pytest.approx(970) and small["upper"] == pytest.approx(1030)
    assert small["score"] == 0 and small["is_anomaly"] is False
    assert native(constant, 1100)["is_anomaly"] is True


@pytest.mark.sandbox
def test_a_rate_moving_off_exactly_zero_stays_an_anomaly():
    """Median 0 gives a floor of 0 (anomaly_mad_floor_abs defaults to 0): any null rate above 0 is flagged."""
    result = native(daily([0.0] * 20), 0.01)
    assert result["upper"] == 0 and result["is_anomaly"] is True


# --- min_consecutive ---------------------------------------------------------------------------------


@pytest.mark.sandbox
def test_min_consecutive_needs_previous_points_out_of_band():
    two_high = daily(NOISY + [150, 150])
    assert native(two_high, 150, min_consecutive=3)["is_anomaly"] is True
    one_high = daily(NOISY + [100, 150])
    result = native(one_high, 150, min_consecutive=3)
    assert result["score"] > 0 and result["is_anomaly"] is False
    assert native(one_high, 150, min_consecutive=2)["is_anomaly"] is True


@pytest.mark.sandbox
def test_min_consecutive_counts_only_breaches_in_the_same_direction():
    two_low = daily(NOISY + [50, 50])
    result = native(two_low, 150, min_consecutive=3)
    assert result["score"] > 0 and result["is_anomaly"] is False


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


@pytest.mark.sandbox
def test_default_observed_at_buckets_by_the_utc_hour():
    """No observed_at: the bucket must be the current UTC hour, whatever the session time zone."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    points = [(now - timedelta(hours=h), 100.0 + 10 * (now - timedelta(hours=h)).hour) for h in range(1, 5 * 7 * 24 + 1)]
    result = native(points, 100.0 + 10 * now.hour)
    after = datetime.now(timezone.utc).hour
    assert result["baseline"] == "seasonal"
    assert result["expected"] in {100.0 + 10 * now.hour, 100.0 + 10 * after}
    assert result["is_anomaly"] is False or after != now.hour
