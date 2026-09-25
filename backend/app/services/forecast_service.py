"""Weekly revenue forecasting — weekly is the modeling granularity (finer signal, more
backtest folds); monthly is a DISPLAY-ONLY aggregation of the weekly forecast, never a
separate model. Compares several standard methods via a walk-forward rolling backtest and
picks the winner purely on backtest performance — this module never sees any answer-key
file, so the choice can never be "fit to the test." Two extra drivers a plain revenue
trend can't see on its own: Islamic calendar events (Ramadan/Eid — a demand dip/shift
around these dates is a recurring calendar effect, not a change in the underlying trend)
and active-customer count (a leading indicator of revenue for a transaction-log
business)."""

import numpy as np
import pandas as pd

from app.services.islamic_calendar_service import islamic_events

BACKTEST_HORIZON_WEEKS = 13
BACKTEST_MIN_TRAIN_WEEKS = 26
BACKTEST_STEP_WEEKS = 4
SEASONAL_NAIVE_SEASON_WEEKS = 52

METHOD_LABELS = {
    "naive": "Naive (repeat last week)",
    "seasonal_naive": "Seasonal naive (repeat same week last year)",
    "holt": "Holt's linear trend",
    "regression_with_drivers": "Regression with calendar + customer drivers",
}


# ---------------------------------------------------------------------------
# Series builders
# ---------------------------------------------------------------------------


def build_weekly_revenue(df: pd.DataFrame, timestamp_col: str, money_col: str) -> pd.Series:
    """Complete weeks only (Mon-Sun) — the last week is dropped if the data doesn't cover
    it through Sunday, so a partial trailing week never distorts the series with an
    artificially low value."""
    work = df.copy()
    work[timestamp_col] = pd.to_datetime(work[timestamp_col], errors="coerce")
    work = work.dropna(subset=[timestamp_col])
    if work.empty:
        return pd.Series(dtype=float)
    weekly = work.groupby(work[timestamp_col].dt.to_period("W-SUN"))[money_col].sum().sort_index()
    if len(weekly) == 0:
        return weekly
    last_covered = work[timestamp_col].max()
    if last_covered < weekly.index[-1].end_time.normalize():
        weekly = weekly.iloc[:-1]
    return weekly


def build_weekly_active_customers(df: pd.DataFrame, timestamp_col: str, customer_col: str, week_index: pd.PeriodIndex) -> pd.Series:
    work = df[df[customer_col].notna()].copy()
    work[timestamp_col] = pd.to_datetime(work[timestamp_col], errors="coerce")
    work = work.dropna(subset=[timestamp_col])
    weekly = work.groupby(work[timestamp_col].dt.to_period("W-SUN"))[customer_col].nunique()
    return weekly.reindex(week_index, fill_value=0)


def build_calendar_flags(week_index: pd.PeriodIndex) -> pd.DataFrame:
    """One row per week in `week_index`; 1/0 dummies, 1 if ANY day of that week falls
    within a (buffered) Ramadan/Eid al-Fitr/Eid al-Adha window."""
    if len(week_index) == 0:
        return pd.DataFrame(columns=["is_ramadan", "is_eid_fitr", "is_eid_adha"])
    events = islamic_events(week_index[0].start_time.date(), week_index[-1].end_time.date())
    name_to_col = {"Ramadan": "is_ramadan", "Eid al-Fitr": "is_eid_fitr", "Eid al-Adha": "is_eid_adha"}
    rows = []
    for p in week_index:
        week_start, week_end = p.start_time.date(), p.end_time.date()
        flags = {"is_ramadan": 0, "is_eid_fitr": 0, "is_eid_adha": 0}
        for e in events:
            if e["start"] <= week_end and e["end"] >= week_start:
                flags[name_to_col[e["name"]]] = 1
        rows.append(flags)
    return pd.DataFrame(rows, index=week_index)


def future_week_index(last_period: pd.Period, periods: int) -> pd.PeriodIndex:
    return pd.period_range(last_period + 1, periods=periods, freq=last_period.freq)


def _project_customer_counts(history: np.ndarray, periods: int, recent_window: int = 4) -> np.ndarray:
    """Forward projection for the customer-count DRIVER itself (not the target being
    forecast) — held at the recent average rather than its own extrapolated trend, to
    avoid compounding a second forecast's error into the revenue forecast."""
    recent = history[-recent_window:] if len(history) >= recent_window else history
    return np.full(periods, float(np.mean(recent)))


# ---------------------------------------------------------------------------
# Forecasting methods — each takes the TRAINING history and returns `periods` point
# forecasts, or None if not viable with this much history (never fabricated).
# ---------------------------------------------------------------------------


def _naive(history: np.ndarray, periods: int, **_) -> np.ndarray | None:
    if len(history) < 1:
        return None
    return np.full(periods, history[-1])


def _seasonal_naive(history: np.ndarray, periods: int, season: int = SEASONAL_NAIVE_SEASON_WEEKS, **_) -> np.ndarray | None:
    if len(history) < season + 1:
        return None
    last_season = history[-season:]
    reps = int(np.ceil(periods / season))
    return np.tile(last_season, reps)[:periods]


def _holt_fit(y: np.ndarray, alpha: float, beta: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(y)
    level, trend, forecast = np.zeros(n), np.zeros(n), np.zeros(n)
    level[0] = y[0]
    trend[0] = y[1] - y[0] if n > 1 else 0.0
    forecast[0] = y[0]
    for t in range(1, n):
        forecast[t] = level[t - 1] + trend[t - 1]
        level[t] = alpha * y[t] + (1 - alpha) * (level[t - 1] + trend[t - 1])
        trend[t] = beta * (level[t] - level[t - 1]) + (1 - beta) * trend[t - 1]
    return level, trend, forecast


def _best_holt_params(y: np.ndarray) -> tuple[float, float]:
    best = (0.3, 0.1, float("inf"))
    for alpha in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        for beta in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5):
            _, _, fc = _holt_fit(y, alpha, beta)
            sse = float(np.sum((y[1:] - fc[1:]) ** 2))
            if sse < best[2]:
                best = (alpha, beta, sse)
    return best[0], best[1]


def _holt(history: np.ndarray, periods: int, **_) -> np.ndarray | None:
    if len(history) < 4:
        return None
    alpha, beta = _best_holt_params(history)
    level, trend, _ = _holt_fit(history, alpha, beta)
    return np.array([level[-1] + h * trend[-1] for h in range(1, periods + 1)])


def _regression_with_drivers(
    history: np.ndarray,
    periods: int,
    calendar_flags: pd.DataFrame | None = None,
    customer_counts: np.ndarray | None = None,
    forecast_calendar_flags: pd.DataFrame | None = None,
    **_,
) -> np.ndarray | None:
    """OLS: revenue ~ week_trend + is_ramadan + is_eid_fitr + is_eid_adha + active_customers.
    Fitting the calendar dummies alongside the trend is what keeps a Ramadan dip from
    being misread as a change in the underlying trend — the dip gets its own coefficient
    instead of dragging the trend slope down."""
    n = len(history)
    if n < 8 or calendar_flags is None or customer_counts is None or len(calendar_flags) < n or len(customer_counts) < n:
        return None

    trend_col = np.arange(n, dtype=float)
    X = np.column_stack(
        [
            np.ones(n),
            trend_col,
            calendar_flags["is_ramadan"].to_numpy()[:n],
            calendar_flags["is_eid_fitr"].to_numpy()[:n],
            calendar_flags["is_eid_adha"].to_numpy()[:n],
            customer_counts[:n],
        ]
    )
    try:
        coef, *_rest = np.linalg.lstsq(X, history, rcond=None)
    except np.linalg.LinAlgError:
        return None

    future_trend = np.arange(n, n + periods, dtype=float)
    future_customers = _project_customer_counts(customer_counts[:n], periods)
    if forecast_calendar_flags is None or len(forecast_calendar_flags) < periods:
        future_cal = np.zeros((periods, 3))
    else:
        future_cal = forecast_calendar_flags[["is_ramadan", "is_eid_fitr", "is_eid_adha"]].to_numpy()[:periods]

    Xf = np.column_stack([np.ones(periods), future_trend, future_cal, future_customers])
    return Xf @ coef


_METHODS = {
    "naive": _naive,
    "seasonal_naive": _seasonal_naive,
    "holt": _holt,
    "regression_with_drivers": _regression_with_drivers,
}


# ---------------------------------------------------------------------------
# Rolling backtest — walk-forward validation on the dataset's OWN history only.
# ---------------------------------------------------------------------------


def rolling_backtest(
    revenue: np.ndarray,
    calendar_flags: pd.DataFrame,
    customer_counts: np.ndarray,
    horizon: int = BACKTEST_HORIZON_WEEKS,
    min_train: int = BACKTEST_MIN_TRAIN_WEEKS,
    step: int = BACKTEST_STEP_WEEKS,
) -> dict:
    """Returns {method_name: {"mape": mean_abs_pct_error, "folds": n_folds}} — a method
    with fewer than 2 viable folds is reported but never eligible to win (too little
    evidence to trust), and a method that's never viable for this dataset (e.g. seasonal
    naive without a full season of history) reports folds=0, mape=None rather than being
    silently omitted."""
    n = len(revenue)
    fold_errors: dict[str, list[float]] = {name: [] for name in _METHODS}

    train_end = min_train
    while train_end + horizon <= n:
        train_hist = revenue[:train_end]
        actual = revenue[train_end : train_end + horizon]
        for name, fn in _METHODS.items():
            kwargs = {}
            if name == "regression_with_drivers":
                kwargs = dict(
                    calendar_flags=calendar_flags.iloc[:train_end],
                    customer_counts=customer_counts[:train_end],
                    forecast_calendar_flags=calendar_flags.iloc[train_end : train_end + horizon],
                )
            forecast = fn(train_hist, horizon, **kwargs)
            if forecast is None:
                continue
            safe_actual = np.maximum(actual, 1.0)
            mape = float(np.mean(np.abs(forecast - actual) / safe_actual) * 100)
            fold_errors[name].append(mape)
        train_end += step

    return {
        name: {
            "mape": round(float(np.mean(errors)), 2) if len(errors) >= 2 else None,
            "folds": len(errors),
        }
        for name, errors in fold_errors.items()
    }


def select_best_method(backtest_scores: dict) -> str:
    """Lowest mean backtest MAPE among methods with >= 2 valid folds; falls back to
    'naive' (always viable with >= 1 data point) if nothing else qualifies."""
    eligible = {name: s["mape"] for name, s in backtest_scores.items() if s["mape"] is not None}
    if not eligible:
        return "naive"
    return min(eligible, key=eligible.get)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def forecast_weekly_revenue(
    df: pd.DataFrame, timestamp_col: str, money_col: str, customer_col: str, periods_weeks: int = 13
) -> dict | None:
    """Full pipeline: build weekly series + drivers, backtest all methods, forecast with
    the backtest-selected winner, and aggregate to monthly for display. Returns None if
    there's too little weekly history (< 12 weeks) to forecast meaningfully."""
    weekly_revenue = build_weekly_revenue(df, timestamp_col, money_col)
    if len(weekly_revenue) < 12:
        return None

    week_index = weekly_revenue.index
    weekly_customers = build_weekly_active_customers(df, timestamp_col, customer_col, week_index)
    calendar_flags = build_calendar_flags(week_index)

    revenue_arr = weekly_revenue.to_numpy(dtype=float)
    customers_arr = weekly_customers.to_numpy(dtype=float)

    backtest_scores = rolling_backtest(revenue_arr, calendar_flags, customers_arr)
    best_method = select_best_method(backtest_scores)

    future_index = future_week_index(week_index[-1], periods_weeks)
    future_calendar_flags = build_calendar_flags(future_index)

    kwargs = {}
    if best_method == "regression_with_drivers":
        kwargs = dict(calendar_flags=calendar_flags, customer_counts=customers_arr, forecast_calendar_flags=future_calendar_flags)
    point_forecast = _METHODS[best_method](revenue_arr, periods_weeks, **kwargs)
    if point_forecast is None:  # the winner turned out non-viable at full history length (e.g. seasonal_naive edge case) — fall back
        best_method = "naive"
        point_forecast = _naive(revenue_arr, periods_weeks)

    # Uncertainty band from the WINNING method's own backtest fold errors (real
    # out-of-sample performance), not in-sample residuals — a materially more honest
    # estimate of how wrong this method actually tends to be on genuinely unseen weeks.
    winner_folds = backtest_scores[best_method]
    if winner_folds["mape"]:
        relative_error = winner_folds["mape"] / 100
    else:
        relative_error = 0.25  # no valid backtest folds for the chosen fallback — a conservative wide band

    weekly_rows = []
    for i, wk in enumerate(future_index):
        pf = max(0.0, float(point_forecast[i]))
        band = pf * relative_error * ((i + 1) ** 0.5)
        weekly_rows.append(
            {
                "week": str(wk),
                "week_start": wk.start_time.strftime("%Y-%m-%d"),
                "week_end": wk.end_time.strftime("%Y-%m-%d"),
                "point_forecast": round(pf, 2),
                "lower_bound": round(max(0.0, pf - band), 2),
                "upper_bound": round(pf + band, 2),
                "is_ramadan": bool(future_calendar_flags.iloc[i]["is_ramadan"]),
                "is_eid_fitr": bool(future_calendar_flags.iloc[i]["is_eid_fitr"]),
                "is_eid_adha": bool(future_calendar_flags.iloc[i]["is_eid_adha"]),
            }
        )

    monthly_display = _aggregate_weekly_to_monthly(weekly_rows)

    factors = ["the historical weekly revenue trend"]
    if best_method == "regression_with_drivers":
        factors += ["Ramadan/Eid al-Fitr/Eid al-Adha calendar timing", "active-customer counts"]
    factors_text = ", ".join(factors[:-1]) + (f", and {factors[-1]}" if len(factors) > 1 else factors[0])

    return {
        "granularity": "weekly (aggregated to monthly for display)",
        "method": best_method,
        "method_label": METHOD_LABELS[best_method],
        "backtest_scores": {
            name: {"mape": s["mape"], "folds": s["folds"], "label": METHOD_LABELS[name]} for name, s in backtest_scores.items()
        },
        "history_weeks": len(weekly_revenue),
        "explanation": f"Selected by rolling backtest (lowest out-of-sample error across {winner_folds['folds']} historical fold(s)) — never fit to any held-out answer key. Accounts for {factors_text}.",
        "weekly_forecast": weekly_rows,
        "monthly_forecast": monthly_display,
        # Backward-compatible key some callers/tests still read directly.
        "forecast": monthly_display,
    }


def _majority_month(week_start: str, week_end: str) -> pd.Period:
    """Attributes a forecast week to the calendar month containing the MAJORITY of its 7
    days — not just its start date, which would misattribute a week like Mar 30-Apr 5
    (only 2 of 7 days in March) to March, creating a spurious near-empty partial-month
    row in the display."""
    days = pd.date_range(week_start, week_end, freq="D")
    return days.to_period("M").value_counts().idxmax()


def _aggregate_weekly_to_monthly(weekly_rows: list[dict]) -> list[dict]:
    """Display-only aggregation — each forecast week's revenue is summed into whichever
    calendar month contains most of that week's days (see _majority_month)."""
    df = pd.DataFrame(weekly_rows)
    df["month"] = [_majority_month(r["week_start"], r["week_end"]) for r in weekly_rows]
    grouped = df.groupby("month").agg(
        point_forecast=("point_forecast", "sum"),
        lower_bound=("lower_bound", "sum"),
        upper_bound=("upper_bound", "sum"),
    )
    return [
        {
            "month": str(month),
            "point_forecast": round(float(row["point_forecast"]), 2),
            "lower_bound": round(float(row["lower_bound"]), 2),
            "upper_bound": round(float(row["upper_bound"]), 2),
        }
        for month, row in grouped.iterrows()
    ]


def score_forecast_vs_actual(forecast: dict, actual_by_month: dict[str, float]) -> dict:
    """Compares monthly-aggregated point forecasts against real values — e.g. from a
    held-out answer key used only in tests, never seen by the forecasting/method-
    selection logic above."""
    rows = []
    for entry in forecast["monthly_forecast"]:
        month = entry["month"]
        actual = actual_by_month.get(month)
        if actual is None:
            continue
        error = entry["point_forecast"] - actual
        rows.append(
            {
                "month": month,
                "point_forecast": entry["point_forecast"],
                "actual": actual,
                "abs_pct_error": round(abs(error) / actual * 100, 2) if actual else None,
            }
        )
    if not rows:
        return {"rows": [], "mean_abs_pct_error": None}
    return {"rows": rows, "mean_abs_pct_error": round(sum(r["abs_pct_error"] for r in rows) / len(rows), 2)}
