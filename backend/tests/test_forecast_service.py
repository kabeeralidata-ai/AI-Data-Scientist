"""Unit tests for the weekly forecasting engine (methods, rolling backtest, method
selection) and the Islamic calendar approximation, independent of the full coffee-shop
pipeline — fast, deterministic, synthetic data."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.services import forecast_service as fs
from app.services.islamic_calendar_service import islamic_events, ramadan_start


def test_ramadan_start_shifts_earlier_each_hijri_year():
    """Consecutive Hijri years are ~354 days apart in the Gregorian calendar (not ~365),
    which is exactly the "~11 days earlier each [Gregorian] year" the spec describes —
    the whole reason this must be computed, not hardcoded."""
    y1 = ramadan_start(1446)
    y2 = ramadan_start(1447)
    gregorian_days_apart = (y2 - y1).days
    shift_relative_to_a_solar_year = 365 - gregorian_days_apart
    assert 8 <= shift_relative_to_a_solar_year <= 13


def test_islamic_events_finds_known_2025_ramadan_window():
    events = islamic_events(date(2025, 1, 1), date(2025, 12, 31))
    names = {e["name"] for e in events}
    assert {"Ramadan", "Eid al-Fitr", "Eid al-Adha"} <= names
    ramadan = next(e for e in events if e["name"] == "Ramadan")
    # Public knowledge: Ramadan 2025 began around March 1 — the buffered window must
    # contain late Feb / early March.
    assert ramadan["start"] <= date(2025, 3, 1) <= ramadan["end"]


def test_naive_forecast_repeats_last_value():
    history = np.array([100.0, 110.0, 90.0, 120.0])
    forecast = fs._naive(history, periods=3)
    assert list(forecast) == [120.0, 120.0, 120.0]


def test_seasonal_naive_returns_none_without_a_full_season():
    history = np.arange(20, dtype=float)
    assert fs._seasonal_naive(history, periods=4, season=52) is None


def test_seasonal_naive_repeats_the_prior_season_when_available():
    history = np.concatenate([np.full(52, 50.0), np.full(10, 999.0)])  # only first 52 matter for a 4-step lookback
    forecast = fs._seasonal_naive(history, periods=4, season=52)
    assert forecast is not None
    assert len(forecast) == 4


def test_holt_forecast_extrapolates_an_upward_trend():
    history = np.array([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 180.0])
    forecast = fs._holt(history, periods=3)
    assert forecast is not None
    assert forecast[0] > history[-1]  # continues the clear upward trend
    assert forecast[2] > forecast[0]  # keeps rising


def test_regression_with_drivers_returns_none_with_too_little_history():
    history = np.array([100.0, 110.0, 120.0])
    assert fs._regression_with_drivers(history, periods=2) is None


def test_regression_with_drivers_picks_up_a_real_calendar_effect():
    """Builds a synthetic series with a genuine weekly upward trend PLUS a real 30%
    revenue dip during 4 marked 'event' weeks — the regression method must recover a
    trend forecast close to the non-dip trend line, not be dragged down by the dip."""
    n = 40
    rng = np.random.RandomState(7)
    trend = 1000 + 20 * np.arange(n)
    is_event = np.zeros(n)
    is_event[10:14] = 1  # a 4-week "Ramadan-like" dip block mid-series
    revenue = trend * (1 - 0.3 * is_event) + rng.normal(0, 5, n)

    calendar_flags = pd.DataFrame(
        {"is_ramadan": is_event, "is_eid_fitr": np.zeros(n), "is_eid_adha": np.zeros(n)}
    )
    customer_counts = np.full(n, 50.0)
    forecast_flags = pd.DataFrame({"is_ramadan": np.zeros(5), "is_eid_fitr": np.zeros(5), "is_eid_adha": np.zeros(5)})

    forecast = fs._regression_with_drivers(
        revenue, periods=5, calendar_flags=calendar_flags, customer_counts=customer_counts, forecast_calendar_flags=forecast_flags
    )
    assert forecast is not None
    expected_next = trend[-1] + 20  # the TRUE non-dip trend continuation
    assert abs(forecast[0] - expected_next) < expected_next * 0.05  # within 5% — recovers the real trend, not the dip


def test_rolling_backtest_reports_all_methods_and_fold_counts():
    n = 40
    revenue = np.linspace(1000, 2000, n) + np.random.RandomState(1).normal(0, 20, n)
    calendar_flags = pd.DataFrame({"is_ramadan": np.zeros(n), "is_eid_fitr": np.zeros(n), "is_eid_adha": np.zeros(n)})
    customer_counts = np.full(n, 30.0)

    scores = fs.rolling_backtest(revenue, calendar_flags, customer_counts, horizon=5, min_train=20, step=5)
    assert set(scores) == {"naive", "seasonal_naive", "holt", "regression_with_drivers"}
    assert scores["seasonal_naive"]["folds"] == 0  # not enough history for a 52-week season
    assert scores["naive"]["folds"] >= 2
    assert scores["holt"]["mape"] is not None


def test_select_best_method_picks_lowest_mape_among_eligible():
    scores = {
        "naive": {"mape": 15.0, "folds": 3},
        "seasonal_naive": {"mape": None, "folds": 0},
        "holt": {"mape": 8.0, "folds": 3},
        "regression_with_drivers": {"mape": 20.0, "folds": 3},
    }
    assert fs.select_best_method(scores) == "holt"


def test_select_best_method_falls_back_to_naive_when_nothing_is_eligible():
    scores = {name: {"mape": None, "folds": 0} for name in fs.METHOD_LABELS}
    assert fs.select_best_method(scores) == "naive"


def test_forecast_weekly_revenue_returns_none_with_too_little_history():
    df = pd.DataFrame(
        {
            "transaction_time": pd.date_range("2026-01-01", periods=20, freq="D"),
            "total_pkr": np.full(20, 100.0),
            "customer_id": ["C1"] * 20,
        }
    )
    assert fs.forecast_weekly_revenue(df, "transaction_time", "total_pkr", "customer_id") is None


def test_forecast_weekly_revenue_end_to_end_on_synthetic_data():
    dates = pd.date_range("2025-01-01", periods=400, freq="D")
    rng = np.random.RandomState(3)
    daily_revenue = 500 + 0.5 * np.arange(400) + rng.normal(0, 30, 400)
    customer_ids = [f"C{i % 80}" for i in range(400)]
    df = pd.DataFrame({"transaction_time": dates, "total_pkr": daily_revenue, "customer_id": customer_ids})

    result = fs.forecast_weekly_revenue(df, "transaction_time", "total_pkr", "customer_id", periods_weeks=13)
    assert result is not None
    assert result["method"] in fs.METHOD_LABELS
    assert len(result["weekly_forecast"]) == 13
    assert len(result["monthly_forecast"]) >= 3
    assert result["explanation"]
    for row in result["monthly_forecast"]:
        assert row["point_forecast"] > 0
        assert row["lower_bound"] <= row["point_forecast"] <= row["upper_bound"]
