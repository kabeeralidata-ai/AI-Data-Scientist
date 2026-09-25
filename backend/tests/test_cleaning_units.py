"""Direct unit tests for the cleaning helpers added to fix distance_km ("4.2 km") being
treated as text, inconsistent categorical casing, and impossible negative values."""

import pandas as pd

from app.services.cleaning_service import (
    _count_invalid_dates,
    _looks_like_date_column,
    _mixed_type_fraction,
    _normalize_categorical_text_case,
    _replace_impossible_negative_values,
    _strip_units_from_numeric_like_columns,
)


def test_strip_units_converts_km_suffixed_column_to_numeric():
    df = pd.DataFrame({"distance_km": ["2.92", "7.23", "1.0 km", "5.92 km", "3.2 km"]})
    log = {}
    result = _strip_units_from_numeric_like_columns(df, log)

    assert pd.api.types.is_numeric_dtype(result["distance_km"])
    assert result["distance_km"].tolist() == [2.92, 7.23, 1.0, 5.92, 3.2]
    assert "distance_km" in log["unit_stripped_columns"]


def test_strip_units_handles_currency_and_percent_and_commas():
    df = pd.DataFrame({
        "price": ["PKR 500", "$20", "1,234.50", "Rs. 99"],
        "discount": ["10%", "5%", "0%", "25%"],
    })
    log = {}
    result = _strip_units_from_numeric_like_columns(df, log)

    assert result["price"].tolist() == [500.0, 20.0, 1234.50, 99.0]
    assert result["discount"].tolist() == [10.0, 5.0, 0.0, 25.0]


def test_strip_units_leaves_genuinely_categorical_columns_alone():
    df = pd.DataFrame({"vehicle_type": ["Car", "Motorbike", "Bicycle", "Car"]})
    log = {}
    result = _strip_units_from_numeric_like_columns(df, log)

    assert not pd.api.types.is_numeric_dtype(result["vehicle_type"])
    assert "unit_stripped_columns" not in log


def test_strip_units_requires_a_high_match_rate_not_a_handful_of_coincidences():
    """A column that's mostly genuine text with a couple of numeric-looking entries must
    not be converted — only a column that's overwhelmingly number-with-unit."""
    df = pd.DataFrame({"note": ["fragile", "handle with care", "5 km", "urgent"]})
    log = {}
    result = _strip_units_from_numeric_like_columns(df, log)

    assert not pd.api.types.is_numeric_dtype(result["note"])
    assert "unit_stripped_columns" not in log


def test_normalize_categorical_text_case_merges_case_variants():
    df = pd.DataFrame({"weather": ["Rainy", "rainy", "Rainy", "Clear", "clear", "Rainy"]})
    log = {}
    result = _normalize_categorical_text_case(df, log)

    # Only one casing per underlying value should remain.
    assert set(result["weather"].unique()) == {"Rainy", "Clear"}
    assert "weather" in log["text_case_normalized_columns"]


def test_normalize_categorical_text_case_picks_the_most_frequent_casing():
    df = pd.DataFrame({"status": ["done", "done", "done", "Done"]})
    log = {}
    result = _normalize_categorical_text_case(df, log)
    assert set(result["status"].unique()) == {"done"}


def test_normalize_categorical_text_case_leaves_consistent_columns_alone():
    df = pd.DataFrame({"vehicle_type": ["Car", "Motorbike", "Bicycle"]})
    log = {}
    result = _normalize_categorical_text_case(df, log)
    assert result["vehicle_type"].tolist() == ["Car", "Motorbike", "Bicycle"]
    assert "text_case_normalized_columns" not in log


def test_replace_impossible_negative_values_when_negatives_are_rare():
    # 98 non-negative values plus 2 negative ones (2%, well under the 5% "rare" bar) —
    # matching the real dataset, where 3 negative delivery times out of 2520 rows (0.12%)
    # are corrected.
    values = [30, 45, 20, 60, 25, 40, 35, 50] * 12 + [30, 45, -1, -3]
    df = pd.DataFrame({"delivery_time_min": values})
    log = {}
    result = _replace_impossible_negative_values(df, log)

    assert (result["delivery_time_min"].dropna() >= 0).all()
    assert result["delivery_time_min"].isna().sum() == 2
    assert log["impossible_negative_values_corrected"]["delivery_time_min"] == 2


def test_replace_impossible_negative_values_does_nothing_when_negatives_are_common():
    """A column where 20% of values are negative isn't a 'rare error' pattern — it's a
    genuinely mixed-sign measurement, so it must be left untouched (same principle as the
    profit/loss test below, just closer to the 5% boundary)."""
    values = [30, 45, 20, -1, 60, 25, -3, 40, 35, 50] * 5  # 20% negative
    df = pd.DataFrame({"delivery_time_min": values})
    log = {}
    result = _replace_impossible_negative_values(df, log)

    assert (result["delivery_time_min"] < 0).sum() == 10
    assert "impossible_negative_values_corrected" not in log


def test_replace_impossible_negative_values_leaves_genuinely_mixed_sign_columns_alone():
    """A column where negatives are common (e.g. profit/loss) must not be touched — only
    a column where negatives are rare exceptions counts as 'impossible'."""
    df = pd.DataFrame({"profit": [100, -50, 200, -80, 30, -20, 150, -10, 90, -60]})
    log = {}
    result = _replace_impossible_negative_values(df, log)

    assert (result["profit"] < 0).sum() == 5
    assert "impossible_negative_values_corrected" not in log


def test_replace_impossible_negative_values_never_touches_a_refund_capable_column():
    """Regression test (previous review finding): a transaction 'quantity' column where
    only 2% of values are negative used to get wiped to NaN by the rare-negative
    heuristic above — even though those negatives are real refunds, not data-entry
    errors. column_roles (from data_understanding_service) marking a column
    can_be_negative=True must make it exempt regardless of how rare the negatives are."""
    values = [1, 2, 1, 3, 2, 1, 4, 2, 1, 3] * 10 + [-1, -2]  # 2% negative, same rarity as the "rare" test above
    df = pd.DataFrame({"quantity": values})
    column_roles = {"quantity": {"role": "quantity", "can_be_negative": True}}
    log = {}
    result = _replace_impossible_negative_values(df, log, column_roles)

    assert (result["quantity"] < 0).sum() == 2  # preserved, not nulled out
    assert "impossible_negative_values_corrected" not in log


def test_looks_like_date_column_matches_name_hints():
    assert _looks_like_date_column("order_date")
    assert _looks_like_date_column("signup_time")
    assert not _looks_like_date_column("unit_price")


def test_count_invalid_dates_flags_a_minority_of_malformed_entries():
    values = ["2024-01-05", "2024-02-14", "2024-03-01", "not-a-date", "also-bad"] + ["2024-04-0" + str(i) for i in range(1, 10)]
    series = pd.Series(values)
    assert _count_invalid_dates(series) == 2


def test_count_invalid_dates_returns_none_when_column_is_not_really_dates():
    """A column merely NAMED like a date but containing weekday names (not real dates)
    must not have every single value flagged as 'invalid' — that would just mean the name
    hint mismatched the actual column contents, not a real invalid-date problem."""
    series = pd.Series(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"] * 3)
    assert _count_invalid_dates(series) is None


def test_count_invalid_dates_returns_none_when_all_values_are_valid():
    series = pd.Series(["2024-01-0" + str(i) for i in range(1, 8)])
    assert _count_invalid_dates(series) is None


def test_mixed_type_fraction_detects_inconsistent_numeric_and_text_entries():
    values = ["123", "456", "hello", "world", "789", "foo", "bar", "42", "baz", "99"]
    series = pd.Series(values)
    fraction = _mixed_type_fraction(series)
    assert fraction is not None
    assert 0.1 <= fraction <= 0.9


def test_mixed_type_fraction_returns_none_for_purely_numeric_or_purely_text_columns():
    numeric_series = pd.Series([str(i) for i in range(20)])
    assert _mixed_type_fraction(numeric_series) is None

    text_series = pd.Series(["apple", "banana", "cherry"] * 7)
    assert _mixed_type_fraction(text_series) is None
