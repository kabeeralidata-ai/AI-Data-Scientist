import os
import re

import pandas as pd
from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.models.dataset import Dataset
from app.schemas.dataset import CleaningRequest
from app.services.dataset_service import (
    DATE_HINT_KEYWORDS,
    build_dataset_profile,
    load_original_dataframe,
    sync_dataset_columns,
    tokenize_column_name,
)
from app.utils.validators import DatasetValidationError

VALID_STRATEGIES = {"drop", "mean", "median", "mode", "constant", "auto"}
OUTLIER_IQR_MULTIPLIER = 1.5

# Matches a number (optionally with thousands commas) with an optional leading currency
# symbol/code or trailing unit — e.g. "4.2 km", "15%", "PKR 500", "$20", "1,234.5".
_UNIT_NUMBER_PATTERN = re.compile(
    r"^\s*(?:rs\.?|pkr|usd|\$|€|£)?\s*([+-]?[\d,]+\.?\d*)\s*"
    r"(?:km/h|km|kg|g|lbs?|mi|m|%|percent|pkr|rs\.?|usd|\$|hrs?|hours?|mins?|minutes?)?\s*$",
    re.IGNORECASE,
)
UNIT_STRIP_MATCH_RATE_THRESHOLD = 0.8

# A numeric measurement (a duration, distance, price, count...) that's ALMOST ALWAYS
# non-negative but has a handful of negative values is very likely a data-entry error
# (e.g. a delivery that took "-1" minutes), not real signal — unlike a column that's
# genuinely mixed-sign (profit/loss, temperature), where negatives are common, not rare.
IMPOSSIBLE_NEGATIVE_MAX_FRACTION = 0.05

# A plausibility UPPER bound is deliberately restricted to cases with a genuine, universal
# DOMAIN ceiling — never a generic "far from the bulk of the distribution" statistic. That
# was tried and rejected: probed against all 6 real benchmark datasets, a pure statistical
# extreme-value test also flagged legitimately rare (not impossible) values — a $482
# monthly spend, a 144-unit bulk order, a 350-minute delivery — which have no hard ceiling
# and would be wrongly nulled. Only two column SHAPES have a real, checkable physical/
# biological ceiling regardless of dataset: a person's age, and a count of one time unit
# within a bounded time period (e.g. minutes within a month — a month has a fixed maximum
# number of minutes no matter what the data says).
AGE_NAME_TOKEN = "age"
AGE_MAX_PLAUSIBLE = 120  # the oldest medically verified human age on record is ~122

TIME_UNIT_MINUTES = {
    "second": 1 / 60, "seconds": 1 / 60, "sec": 1 / 60,
    "minute": 1, "minutes": 1, "min": 1, "mins": 1,
    "hour": 60, "hours": 60, "hr": 60, "hrs": 60,
}
TIME_PERIOD_MINUTES = {
    "day": 24 * 60,
    "week": 7 * 24 * 60,
    "month": 31 * 24 * 60,  # longest calendar month, so a real 28/30-day month is never falsely flagged
    "year": 366 * 24 * 60,  # leap year, for the same reason
}


def _detect_implausible_age_values(df: pd.DataFrame) -> dict[str, dict]:
    """A column literally named/tokenized as 'age' cannot plausibly exceed
    AGE_MAX_PLAUSIBLE — a near-universal biological fact, not a per-dataset guess.
    Negative ages are already caught by _detect_impossible_negative_values; this only
    checks the upper bound."""
    found: dict[str, dict] = {}
    for col in df.select_dtypes(include="number").columns:
        tokens = tokenize_column_name(str(col))
        if AGE_NAME_TOKEN not in tokens:
            continue
        series = df[col]
        mask = series > AGE_MAX_PLAUSIBLE
        count = int(mask.sum())
        if count > 0:
            found[str(col)] = {"count": count, "bound": AGE_MAX_PLAUSIBLE, "kind": "age"}
    return found


def _time_quantity_ceiling(col_name: str) -> float | None:
    """If a column name names a time UNIT within a time PERIOD (e.g. 'call_minutes_month'
    -> minutes within a month), returns the maximum value physically possible in that
    column's own units — e.g. 31 days x 24 x 60 = 44,640 minutes in a month. Returns None
    for any column that doesn't match this specific unit-within-period shape."""
    tokens = tokenize_column_name(col_name)
    unit_minutes = next((TIME_UNIT_MINUTES[t] for t in tokens if t in TIME_UNIT_MINUTES), None)
    period_minutes = next((TIME_PERIOD_MINUTES[t] for t in tokens if t in TIME_PERIOD_MINUTES), None)
    if unit_minutes is None or period_minutes is None:
        return None
    return period_minutes / unit_minutes


def _detect_implausible_time_quantity_values(df: pd.DataFrame) -> dict[str, dict]:
    """See _time_quantity_ceiling — flags a value that exceeds the physically possible
    maximum for a time-unit-within-a-time-period column (e.g. more call minutes in a
    month than the month actually has)."""
    found: dict[str, dict] = {}
    for col in df.select_dtypes(include="number").columns:
        ceiling = _time_quantity_ceiling(str(col))
        if ceiling is None:
            continue
        series = df[col]
        mask = series > ceiling
        count = int(mask.sum())
        if count > 0:
            found[str(col)] = {"count": count, "bound": ceiling, "kind": "time_quantity"}
    return found


def _detect_implausible_large_values(df: pd.DataFrame) -> dict[str, dict]:
    """Combines the age and time-quantity ceilings into one lookup — see each detector's
    docstring for why an upper plausibility bound is restricted to these two genuinely
    universal, dataset-agnostic cases rather than a generic statistical outlier test."""
    found = _detect_implausible_age_values(df)
    found.update(_detect_implausible_time_quantity_values(df))
    return found


def _replace_implausible_large_values(df: pd.DataFrame, log: dict) -> pd.DataFrame:
    corrected = _detect_implausible_large_values(df)
    for col, info in corrected.items():
        mask = df[col] > info["bound"]
        df[col] = df[col].astype("float64")
        df.loc[mask, col] = float("nan")

    if corrected:
        log["implausible_large_values_corrected"] = {k: v["count"] for k, v in corrected.items()}
    return df


def _strip_units_from_numeric_like_columns(df: pd.DataFrame, log: dict) -> pd.DataFrame:
    """Converts text columns that are actually numbers-with-a-unit (distance_km values
    like '4.2 km', a percentage, a currency amount) into real numeric columns, instead of
    silently treating the whole column as categorical text because a few rows have a unit
    suffix. Only converts a column when the vast majority of its non-null values match the
    pattern, so a genuinely categorical column full of short strings isn't misfired on.
    """
    converted: dict[str, float] = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        series = df[col]
        non_null = series.dropna().astype(str)
        if len(non_null) == 0:
            continue
        matches = non_null.str.match(_UNIT_NUMBER_PATTERN)
        match_rate = float(matches.mean())
        if match_rate < UNIT_STRIP_MATCH_RATE_THRESHOLD:
            continue

        def _extract(val):
            if pd.isna(val):
                return None
            m = _UNIT_NUMBER_PATTERN.match(str(val))
            if not m:
                return None
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None

        df[col] = series.apply(_extract)
        converted[str(col)] = round(match_rate, 4)

    if converted:
        log["unit_stripped_columns"] = converted
    return df


def _normalize_categorical_text_case(df: pd.DataFrame, log: dict) -> pd.DataFrame:
    """Merges values that differ only by letter case (e.g. 'rainy' and 'Rainy') into one
    canonical form — the most frequently occurring casing for that value — so they aren't
    silently treated as different categories in EDA, cleaning, and training."""
    normalized: dict[str, dict[str, str]] = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        non_null = df[col].dropna().astype(str)
        if len(non_null) == 0:
            continue
        value_counts = non_null.value_counts()
        groups: dict[str, list[str]] = {}
        for val in value_counts.index:
            groups.setdefault(val.lower(), []).append(val)

        canonical_map: dict[str, str] = {}
        for variants in groups.values():
            if len(variants) <= 1:
                continue
            best = max(variants, key=lambda v: value_counts[v])
            for variant in variants:
                if variant != best:
                    canonical_map[variant] = best

        if canonical_map:
            df[col] = df[col].map(lambda v: canonical_map.get(v, v) if pd.notna(v) else v)
            normalized[str(col)] = canonical_map

    if normalized:
        log["text_case_normalized_columns"] = normalized
    return df


def _negative_capable_columns(column_roles: dict[str, dict] | None) -> set[str]:
    """Columns the Data Understanding step determined CAN legitimately be negative (money/
    quantity roles in a transaction-style dataset — a refund reduces both). These are
    never treated as data-entry errors, regardless of how rare the negatives are."""
    return {name for name, info in (column_roles or {}).items() if info.get("can_be_negative")}


def _detect_impossible_negative_values(df: pd.DataFrame, column_roles: dict[str, dict] | None = None) -> dict[str, int]:
    """A negative value in a numeric column is treated as a likely data-entry error only
    when negatives are RARE in that column (<=5% of its non-null values) — a principled,
    column-relative bar rather than a hardcoded list of "duration" column names, so a
    genuinely mixed-sign column (profit, temperature) is left alone. Columns the Data
    Understanding step flagged as negative-capable (money/quantity in a transaction
    dataset — refunds) are skipped entirely, even if negatives happen to be rare there
    too, since rarity doesn't make a real refund a data-entry error. Pure detection (no
    mutation) — shared by the pre-cleaning quality report and the cleaning step below."""
    found: dict[str, int] = {}
    exempt = _negative_capable_columns(column_roles)
    for col in df.select_dtypes(include="number").columns:
        if str(col) in exempt:
            continue
        series = df[col]
        non_null = series.dropna()
        if len(non_null) == 0:
            continue
        negative_count = int((series < 0).sum())
        if negative_count == 0:
            continue
        if (negative_count / len(non_null)) <= IMPOSSIBLE_NEGATIVE_MAX_FRACTION:
            found[str(col)] = negative_count
    return found


def _detect_refunds(df: pd.DataFrame, column_roles: dict[str, dict] | None) -> dict[str, dict]:
    """For each negative-capable (money/quantity) column that actually has negative
    values, reports the count and total — surfaced as real refund/return signal, never
    silently dropped or corrected away like the impossible-negative-value case above."""
    refunds: dict[str, dict] = {}
    for col in _negative_capable_columns(column_roles):
        if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        negative_mask = df[col] < 0
        count = int(negative_mask.sum())
        if count == 0:
            continue
        refunds[col] = {"count": count, "total": float(df.loc[negative_mask, col].sum())}
    return refunds


def _replace_impossible_negative_values(df: pd.DataFrame, log: dict, column_roles: dict[str, dict] | None = None) -> pd.DataFrame:
    corrected = _detect_impossible_negative_values(df, column_roles)
    for col, negative_count in corrected.items():
        negative_mask = df[col] < 0
        # A plain int64 column can't hold pd.NA/NaN in place — assigning it silently
        # no-ops instead of raising, leaving the impossible values untouched. Casting to
        # float64 first (safe: NaN is a valid float) makes the assignment real.
        df[col] = df[col].astype("float64")
        df.loc[negative_mask, col] = float("nan")

    if corrected:
        log["impossible_negative_values_corrected"] = corrected
    return df


def _outlier_bounds(series: pd.Series) -> tuple[float, float] | None:
    clean = series.dropna()
    if len(clean) < 4:
        return None
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return None
    return float(q1 - OUTLIER_IQR_MULTIPLIER * iqr), float(q3 + OUTLIER_IQR_MULTIPLIER * iqr)


def _looks_like_date_column(name: str) -> bool:
    lowered = str(name).lower()
    return any(k in lowered for k in DATE_HINT_KEYWORDS)


OUT_OF_RANGE_DATE_BUFFER = pd.Timedelta(days=365)


def flag_invalid_dates(series: pd.Series, reference_date: pd.Timestamp | None = None) -> pd.Series:
    """Boolean mask (indexed like `series`) that's True where a value is either
    unparseable, in the future relative to `reference_date` (defaults to now — "the
    upload date" for a freshly-uploaded dataset), or implausibly far (>1 year) outside the
    bulk (1st-99th percentile) of the column's OWN parseable date range — e.g. a
    2031/2099 date sitting among otherwise-2025/2026 transactions. A syntactically valid
    date can still be operationally invalid; this catches both. Reused by the quality
    report (to count/describe the problem) AND by time-based analysis — EDA trend charts,
    forecasting, and the return-prediction cutoff — so invalid dates are excluded
    everywhere they'd otherwise silently distort a result, not just flagged once and
    forgotten."""
    parsed = pd.to_datetime(series, errors="coerce")
    invalid = parsed.isna() & series.notna()

    valid_parsed = parsed.dropna()
    if len(valid_parsed) >= 5:
        reference = reference_date or pd.Timestamp.now()
        after_reference = parsed > reference

        lower_bound = valid_parsed.quantile(0.01) - OUT_OF_RANGE_DATE_BUFFER
        upper_bound = valid_parsed.quantile(0.99) + OUT_OF_RANGE_DATE_BUFFER
        far_outside_range = (parsed < lower_bound) | (parsed > upper_bound)

        invalid = invalid | after_reference.fillna(False) | far_outside_range.fillna(False)
    return invalid


def _count_invalid_dates(series: pd.Series) -> int | None:
    """Only reports a count when a MAJORITY of the column's non-null values parse as
    real dates — otherwise a name hint alone (e.g. a 'day' column of weekday names, not
    actual dates) would falsely flag every single value as an "invalid date". Counts BOTH
    unparseable values and syntactically-valid-but-implausible ones (future/far-outside-
    range — see flag_invalid_dates)."""
    non_null = series.dropna()
    if len(non_null) < 5:
        return None
    parse_rate = pd.to_datetime(non_null, errors="coerce").notna().mean()
    if parse_rate < 0.5:
        return None
    invalid_mask = flag_invalid_dates(non_null)
    invalid = int(invalid_mask.sum())
    if invalid == 0 or invalid >= len(non_null):
        return None
    return invalid


MIXED_TYPE_MIN_FRACTION = 0.1
MIXED_TYPE_MAX_FRACTION = 0.9


def _mixed_type_fraction(series: pd.Series) -> float | None:
    """A text column where a meaningful-but-not-dominant share of values are actually
    numbers-as-text (10-90%) signals inconsistent data entry — not simply a column that
    happens to be mostly numeric text (handled elsewhere by unit-stripping) or mostly
    genuine text (no issue)."""
    non_null = series.dropna().astype(str)
    if len(non_null) < 10:
        return None
    numeric_like = pd.to_numeric(non_null, errors="coerce").notna()
    fraction = float(numeric_like.mean())
    if MIXED_TYPE_MIN_FRACTION <= fraction <= MIXED_TYPE_MAX_FRACTION:
        return fraction
    return None


def _best_grouping_column(df: pd.DataFrame, column_roles: dict[str, dict] | None) -> str | None:
    """Picks a category-role column with sane cardinality (2-50 distinct values) to group
    by for domain-aware outlier detection — e.g. a bakery bulk order and a single espresso
    are both normal WITHIN their own category, even though one looks like a global
    outlier. Returns None when no such column exists (falls back to a global check)."""
    for name, info in (column_roles or {}).items():
        if info.get("role") != "category" or name not in df.columns:
            continue
        nunique = df[name].nunique(dropna=True)
        if 2 <= nunique <= 50:
            return name
    return None


def _grouped_outlier_count(series: pd.Series, group_series: pd.Series | None) -> tuple[int, tuple[float, float] | None]:
    """Global IQR bounds when there's no sensible grouping column; otherwise sums outliers
    computed WITHIN each group (a value only counts as an outlier relative to its own
    peers), which is what keeps a legitimately larger multi-item order from being flagged
    just because it's unusual across the WHOLE dataset rather than within its category."""
    if group_series is None:
        bounds = _outlier_bounds(series)
        if not bounds:
            return 0, None
        lower, upper = bounds
        return int(((series < lower) | (series > upper)).sum()), bounds

    total = 0
    for _, group in series.groupby(group_series):
        bounds = _outlier_bounds(group)
        if not bounds:
            continue
        lower, upper = bounds
        total += int(((group < lower) | (group > upper)).sum())
    return total, None


def analyze_quality(dataset: Dataset, column_roles: dict[str, dict] | None = None) -> list[dict]:
    """Real, computed data-quality findings (never fabricated) with a recommended, reversible fix each.
    `column_roles` (from data_understanding_service.classify_columns) makes this dataset-
    aware where it matters: refunds are reported as refunds rather than "impossible"
    negatives, missing identifiers get an identifier-appropriate recommendation instead of
    "fill with the most common value", and quantity/money outliers are checked within
    their natural category rather than globally. Fully backward compatible — omitting
    column_roles reproduces the prior, dataset-agnostic behavior exactly."""
    df = load_original_dataframe(dataset)
    issues: list[dict] = []

    duplicate_count = int(df.duplicated().sum())
    if duplicate_count > 0:
        issues.append(
            {
                "id": "duplicates",
                "column": None,
                "type": "duplicate_rows",
                "severity": "medium" if duplicate_count / max(len(df), 1) < 0.1 else "high",
                "message": f"{duplicate_count} duplicate row(s) detected.",
                "affected_count": duplicate_count,
                "recommended_fix": {"action": "remove_duplicates"},
            }
        )

    impossible_negatives = _detect_impossible_negative_values(df, column_roles)
    implausible_large_values = _detect_implausible_large_values(df)
    refunds = _detect_refunds(df, column_roles)
    for col, info in refunds.items():
        issues.append(
            {
                "id": f"refunds::{col}",
                "column": col,
                "type": "refunds_detected",
                "severity": "low",
                "message": (
                    f"'{col}' has {info['count']} negative value(s) totaling {round(info['total'], 2):,} — "
                    "these are refunds/returns, not data-entry errors, and are kept as-is."
                ),
                "affected_count": info["count"],
                "recommended_fix": {
                    "action": "keep_as_refunds",
                    "reason": "Negative quantities/totals in transaction data represent real refunds — cleaning never removes or corrects them.",
                },
            }
        )

    grouping_column = _best_grouping_column(df, column_roles)

    for col in df.columns:
        series = df[col]
        missing = int(series.isna().sum())
        is_numeric = pd.api.types.is_numeric_dtype(series)
        role_info = (column_roles or {}).get(str(col))
        role = role_info["role"] if role_info else None

        if missing > 0:
            pct = round(missing / len(df) * 100, 1) if len(df) else 0
            if role == "customer_id":
                recommended_fix = {
                    "action": "label_unknown",
                    "label": "Walk-in / Unknown",
                    "reason": "A missing customer ID means an unidentified/walk-in transaction, not a guessable customer — "
                    "never filled with another customer's ID or a mode value.",
                }
            elif role == "identifier":
                recommended_fix = {
                    "action": "never_fill",
                    "reason": "An identifier column is never filled — a fabricated ID would be worse than a missing one.",
                }
            else:
                strategy = "median" if is_numeric else "mode"
                recommended_fix = {
                    "action": "fill_missing",
                    "strategy": strategy,
                    "reason": "Numeric column → fill with median." if is_numeric else "Categorical column → fill with most frequent value (mode).",
                }
            issues.append(
                {
                    "id": f"missing::{col}",
                    "column": str(col),
                    "type": "missing_values",
                    "severity": "high" if pct > 30 else ("medium" if pct > 5 else "low"),
                    "message": f"'{col}' has {missing} missing value(s) ({pct}%)."
                    + (" These are treated as walk-in/unidentified, not filled." if role == "customer_id" else ""),
                    "affected_count": missing,
                    "recommended_fix": recommended_fix,
                }
            )

        if is_numeric:
            if col in impossible_negatives:
                count = impossible_negatives[col]
                issues.append(
                    {
                        "id": f"impossible::{col}",
                        "column": str(col),
                        "type": "impossible_values",
                        "severity": "medium",
                        "message": (
                            f"'{col}' has {count} negative value(s) that are rare exceptions among "
                            "otherwise non-negative values — likely a data-entry error, not real signal."
                        ),
                        "affected_count": count,
                        "recommended_fix": {
                            "action": "review_only",
                            "reason": "Cleaning converts these rare negative values to missing rather than guessing a replacement.",
                        },
                    }
                )

            if col in implausible_large_values:
                info = implausible_large_values[col]
                bound_text = (
                    f"{info['bound']} years" if info["kind"] == "age" else f"{info['bound']:.0f}"
                )
                reason_text = (
                    "no person plausibly exceeds this age"
                    if info["kind"] == "age"
                    else "physically impossible for this time period (more than the period actually contains)"
                )
                issues.append(
                    {
                        "id": f"implausible::{col}",
                        "column": str(col),
                        "type": "impossible_values",
                        "severity": "medium",
                        "message": (
                            f"'{col}' has {info['count']} value(s) above {bound_text} — {reason_text}, "
                            "not real signal."
                        ),
                        "affected_count": info["count"],
                        "recommended_fix": {
                            "action": "review_only",
                            "reason": "Cleaning converts these implausible values to missing rather than guessing a replacement.",
                        },
                    }
                )

            use_group = grouping_column if role in ("money", "quantity") else None
            outlier_count, bounds = _grouped_outlier_count(series, df[use_group] if use_group else None)
            if outlier_count > 0:
                range_text = (
                    f" outside [{round(bounds[0], 2)}, {round(bounds[1], 2)}]"
                    if bounds
                    else f" within its own '{use_group}' group"
                )
                issues.append(
                    {
                        "id": f"outliers::{col}",
                        "column": str(col),
                        "type": "outliers",
                        "severity": "low",
                        "message": f"'{col}' has {outlier_count} statistical outlier(s){range_text}.",
                        "affected_count": int(outlier_count),
                        "recommended_fix": {
                            "action": "review_only",
                            "reason": "Outliers are flagged for review, not auto-removed — they may be valid extreme values."
                            + (f" Checked within each '{grouping_column}' group so normal variation between groups isn't flagged." if bounds is None else ""),
                        },
                    }
                )
        elif role == "timestamp" or _looks_like_date_column(str(col)):
            invalid_count = _count_invalid_dates(series)
            if invalid_count:
                issues.append(
                    {
                        "id": f"invalid_dates::{col}",
                        "column": str(col),
                        "type": "invalid_dates",
                        "severity": "medium",
                        "message": f"'{col}' has {invalid_count} value(s) that are unparseable, in the future, or far outside the rest of the column's date range.",
                        "affected_count": invalid_count,
                        "recommended_fix": {
                            "action": "review_only",
                            "reason": "Invalid/implausible dates are flagged for manual review and excluded from time-based analysis, rather than guessed.",
                        },
                    }
                )
        else:
            mixed_fraction = _mixed_type_fraction(series)
            if mixed_fraction is not None:
                pct = round(mixed_fraction * 100, 1)
                issues.append(
                    {
                        "id": f"mixed_types::{col}",
                        "column": str(col),
                        "type": "mixed_types",
                        "severity": "low",
                        "message": f"'{col}' mixes numeric-looking and text values ({pct}% look numeric) — inconsistent data entry.",
                        "affected_count": int(round(mixed_fraction * series.notna().sum())),
                        "recommended_fix": {
                            "action": "review_only",
                            "reason": "Inconsistent formatting is flagged for manual review rather than guessed.",
                        },
                    }
                )

    return issues


WALK_IN_LABEL = "Walk-in / Unknown"


def clean_dataset(db: Session, dataset: Dataset, request: CleaningRequest, column_roles: dict[str, dict] | None = None) -> Dataset:
    if request.missing_strategy not in VALID_STRATEGIES:
        raise DatasetValidationError(
            f"Unknown cleaning strategy '{request.missing_strategy}'."
        )

    df = load_original_dataframe(dataset)
    original_rows = len(df)
    log: dict = {
        "missing_strategy": request.missing_strategy,
        "remove_duplicates": request.remove_duplicates,
        "columns_targeted": request.columns or "all",
        "rows_before": original_rows,
        "duplicates_removed": 0,
        "per_column_strategy": {},
    }

    # Unit-stripping / text-case normalization / impossible-value correction run first —
    # they can each turn previously-unusable or bogus values into either real numbers or
    # real missing values, so "missing_cells_before" (used for the "filled N missing
    # cells" message) is measured AFTER these run, reflecting what the fill step below
    # actually has to work with.
    df = _strip_units_from_numeric_like_columns(df, log)
    df = _normalize_categorical_text_case(df, log)
    df = _replace_impossible_negative_values(df, log, column_roles)
    df = _replace_implausible_large_values(df, log)
    log["missing_cells_before"] = int(df.isna().sum().sum())

    if column_roles:
        refunds = _detect_refunds(df, column_roles)
        if refunds:
            log["refunds"] = refunds

    target_columns = request.columns or list(df.columns)

    if request.remove_duplicates:
        before = len(df)
        df = df.drop_duplicates()
        log["duplicates_removed"] = before - len(df)

    # Identifier columns (transaction_id, ...) are NEVER filled — a fabricated ID would be
    # worse than a missing one. A missing customer_id specifically means a real, common
    # business case (a walk-in / unidentified purchase), not an error to guess away — it
    # gets a clear label instead, and is excluded from customer-level analysis downstream
    # (see customer_analytics_service) rather than silently attributed to some other real
    # customer via a mode/median fill.
    identifier_columns = {name for name, info in (column_roles or {}).items() if info.get("role") == "identifier"}
    customer_id_columns = {name for name, info in (column_roles or {}).items() if info.get("role") == "customer_id"}

    for col in target_columns:
        if col not in df.columns or df[col].isna().sum() == 0:
            continue

        if col in identifier_columns:
            log["per_column_strategy"][col] = "never_fill"
            continue

        if col in customer_id_columns:
            df[col] = df[col].fillna(WALK_IN_LABEL)
            log["per_column_strategy"][col] = "label_unknown"
            continue

        is_numeric = pd.api.types.is_numeric_dtype(df[col])
        strategy = request.missing_strategy
        if strategy == "auto":
            strategy = "median" if is_numeric else "mode"
        log["per_column_strategy"][col] = strategy

        if strategy == "drop":
            df = df.dropna(subset=[col])
        elif strategy == "mean" and is_numeric:
            df[col] = df[col].fillna(df[col].mean())
        elif strategy == "median" and is_numeric:
            df[col] = df[col].fillna(df[col].median())
        elif strategy == "mode":
            mode_vals = df[col].mode(dropna=True)
            if len(mode_vals) > 0:
                df[col] = df[col].fillna(mode_vals.iloc[0])
        elif strategy == "constant":
            fill_value = request.constant_value if request.constant_value is not None else 0
            df[col] = df[col].fillna(fill_value)
        elif strategy in ("mean", "median") and not is_numeric:
            mode_vals = df[col].mode(dropna=True)
            if len(mode_vals) > 0:
                df[col] = df[col].fillna(mode_vals.iloc[0])

    log["rows_after"] = len(df)
    log["missing_cells_after"] = int(df.isna().sum().sum())

    base_name = os.path.splitext(dataset.original_storage_path or dataset.storage_path)[0]
    cleaned_path = f"{base_name}_cleaned_v{dataset.cleaning_version + 1}.csv"
    df.to_csv(cleaned_path, index=False)

    profile = build_dataset_profile(df)

    dataset.storage_path = cleaned_path
    dataset.file_type = ".csv"
    dataset.row_count = profile["row_count"]
    dataset.column_count = profile["column_count"]
    dataset.missing_values = profile["missing_values"]
    dataset.duplicate_rows = profile["duplicate_rows"]
    dataset.profile_json = profile
    dataset.is_cleaned = True
    dataset.cleaning_version = dataset.cleaning_version + 1
    dataset.cleaning_log_json = log

    sync_dataset_columns(db, dataset.id, profile)

    db.add(
        AnalysisRun(
            dataset_id=dataset.id,
            run_type="cleaning",
            status="completed",
            results_json={**log, "version": dataset.cleaning_version},
        )
    )
    db.commit()
    db.refresh(dataset)
    return dataset
