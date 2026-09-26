import hashlib
import json
import time

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.models.dataset import Dataset
from app.services.cleaning_service import flag_invalid_dates
from app.services.data_understanding_service import classify_columns
from app.services.dataset_service import detect_semantic_type, load_dataframe, score_target_candidates


def _histogram(series: pd.Series, bins: int = 10) -> dict:
    clean = series.dropna()
    if len(clean) == 0:
        return {"bins": [], "counts": []}
    counts, edges = np.histogram(clean, bins=bins)
    return {
        "bins": [round(float(e), 4) for e in edges],
        "counts": [int(c) for c in counts],
    }


def _outlier_summary(series: pd.Series) -> dict:
    clean = series.dropna()
    if len(clean) < 4:
        return {"count": 0, "pct": 0.0, "lower_bound": None, "upper_bound": None}
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = clean[(clean < lower) | (clean > upper)]
    return {
        "count": int(len(outliers)),
        "pct": round(len(outliers) / len(clean) * 100, 2),
        "lower_bound": round(float(lower), 4),
        "upper_bound": round(float(upper), 4),
    }


def run_eda(db: Session, dataset: Dataset) -> dict:
    df = load_dataframe(dataset)

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    categorical_cols = [c for c in df.columns if c not in numeric_cols]

    summary = {
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "numeric_columns": len(numeric_cols),
        "categorical_columns": len(categorical_cols),
        "missing_cells": int(df.isna().sum().sum()),
        "duplicate_rows": int(df.duplicated().sum()),
    }

    descriptive_stats = {}
    distributions = {}
    outliers = {}
    for col in numeric_cols:
        desc = df[col].describe()
        descriptive_stats[col] = {k: (None if pd.isna(v) else round(float(v), 4)) for k, v in desc.items()}
        distributions[col] = _histogram(df[col])
        outliers[col] = _outlier_summary(df[col])

    category_frequency = {}
    for col in categorical_cols[:15]:
        vc = df[col].value_counts(dropna=True).head(10)
        category_frequency[col] = [{"value": str(k), "count": int(v)} for k, v in vc.items()]

    correlation = {"columns": [], "matrix": []}
    if len(numeric_cols) >= 2:
        corr_matrix = df[numeric_cols].corr(numeric_only=True).round(4)
        correlation = {
            "columns": numeric_cols,
            "matrix": corr_matrix.fillna(0).values.tolist(),
        }

    time_trends = {}
    datetime_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    if not datetime_cols:
        # A raw "date"/"time" name-substring match isn't enough on its own — a numeric
        # duration column like "prep_time_min" also contains "time" and, being already
        # numeric, "successfully" parses via pd.to_datetime as bogus epoch-nanosecond
        # timestamps (virtually always >50% non-null), which would wrongly classify it as
        # BOTH a datetime column and a numeric column — selecting it twice via
        # df[[dc, nc]] and crashing on the resulting duplicate-column DataFrame. Reusing
        # detect_semantic_type's numeric-dtype short-circuit rules this out up front.
        for c in df.columns:
            if c in numeric_cols:
                continue
            if detect_semantic_type(df[c], str(c)) == "datetime":
                try:
                    parsed = pd.to_datetime(df[c], errors="coerce")
                except Exception:
                    continue
                if parsed.notna().sum() > 0.5 * len(df):
                    datetime_cols.append(c)
                    df[c] = parsed

    for dc in datetime_cols[:2]:
        for nc in numeric_cols[:3]:
            temp = df[[dc, nc]].dropna()
            if len(temp) == 0:
                continue
            temp = temp.set_index(dc).resample("ME")[nc].mean().dropna()
            if len(temp) > 0:
                time_trends[f"{nc}_by_{dc}"] = {
                    "labels": [str(idx.date()) for idx in temp.index],
                    "values": [round(float(v), 4) for v in temp.values],
                }

    results = {
        "summary": summary,
        "descriptive_stats": descriptive_stats,
        "distributions": distributions,
        "outliers": outliers,
        "category_frequency": category_frequency,
        "correlation": correlation,
        "time_trends": time_trends,
    }

    db.add(
        AnalysisRun(
            dataset_id=dataset.id,
            run_type="eda",
            status="completed",
            results_json=results,
        )
    )
    db.commit()
    return results


# ============================================================================
# Filtered, BI-dashboard-style EDA — auto-selects KPIs and chart types from
# column types/cardinality alone, so it works unchanged for any dataset.
# ============================================================================

DONUT_MAX_CARDINALITY = 6
BAR_MAX_CARDINALITY = 50
RATING_MIN_CARDINALITY = 2
RATING_MAX_CARDINALITY = 10
FILTER_OPTIONS_MAX_CARDINALITY = 50
SPARKLINE_POINTS = 8

_FILTERED_EDA_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 120


def _label(name: str) -> str:
    return " ".join(w.capitalize() for w in str(name).replace("-", "_").split("_") if w)


def _numeric_columns_from_profile(profile: dict) -> list[str]:
    return [c["name"] for c in profile.get("columns", []) if c.get("is_numeric") and not c.get("is_id_like")]


def _categorical_columns_from_profile(profile: dict) -> list[str]:
    return [
        c["name"]
        for c in profile.get("columns", [])
        if c.get("is_categorical") and not c.get("is_id_like")
    ]


def _datetime_columns_from_profile(profile: dict) -> list[str]:
    return [c["name"] for c in profile.get("columns", []) if c.get("is_datetime")]


def _eda_cache_key(dataset_id, cleaning_version, categorical_filters: dict, date_filters: dict) -> str:
    payload = json.dumps(
        {"categorical": categorical_filters or {}, "date": date_filters or {}}, sort_keys=True, default=str
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{dataset_id}:{cleaning_version}:{digest}"


def _apply_filters(df: pd.DataFrame, categorical_filters: dict | None, date_filters: dict | None) -> pd.DataFrame:
    filtered = df
    for col, values in (categorical_filters or {}).items():
        if col in filtered.columns and values:
            allowed = {str(v) for v in values}
            filtered = filtered[filtered[col].astype(str).isin(allowed)]
    for col, date_range in (date_filters or {}).items():
        if col not in filtered.columns or not date_range:
            continue
        parsed = pd.to_datetime(filtered[col], errors="coerce")
        mask = pd.Series(True, index=filtered.index)
        start = date_range.get("start")
        end = date_range.get("end")
        if start:
            mask &= parsed >= pd.to_datetime(start)
        if end:
            mask &= parsed <= pd.to_datetime(end)
        filtered = filtered[mask]
    return filtered


def _kpi_aggregate(df: pd.DataFrame, kpi: dict) -> float:
    agg = kpi.get("agg")
    col = kpi.get("source_column")
    if agg is None or col is None or col not in df.columns:
        return float(len(df))
    if agg in ("mean", "sum"):
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(series) == 0:
            return 0.0
        return float(series.mean()) if agg == "mean" else float(series.sum())
    if agg == "rate":
        series = df[col].dropna().astype(str)
        if len(series) == 0:
            return 0.0
        return float((series == kpi.get("rate_value")).mean())
    return float(len(df))


_POSITIVE_RATE_TOKENS = ("1", "yes", "true", "y")


def _pick_rate_class(vc: pd.Series) -> tuple[str, bool]:
    """Prefers a conventional 'positive/event' encoding (1/yes/true) over raw
    majority-class share for the headline rate KPI: for a binary outcome flag
    (churn, late_delivery, converted...) the rate that reads naturally in a KPI
    is the rate of the event happening, not whichever class happens to be more
    common — "Churn Rate" should mean the churn rate, not "0 Churn Rate" meaning
    the retention rate just because retained customers are the majority."""
    for token in _POSITIVE_RATE_TOKENS:
        for original in vc.index:
            if str(original).lower() == token:
                return original, True
    return vc.index[0], False


_PERCENT_NAME_TOKENS = ("pct", "percent", "percentage", "rate", "share", "ratio")


def _looks_like_percentage(col_name: str, series: pd.Series) -> bool:
    """A column whose NAME suggests a percentage/rate/share AND whose actual values fall
    within a plausible percentage range (0-100, or 0-1 for a fraction) — both signals
    together, so a genuine count column that happens to say 'rate' in its name (rare) but
    holds values in the thousands isn't misclassified."""
    tokens = str(col_name).lower().replace("-", "_").split("_")
    if not any(t in _PERCENT_NAME_TOKENS for t in tokens):
        return False
    if len(series) == 0:
        return False
    return bool(((series >= 0) & (series <= 100)).all())


def _select_kpis(
    df: pd.DataFrame, profile: dict, description: str | None, column_roles: dict[str, dict] | None = None
) -> list[dict]:
    """Picks 4 KPIs generically: total rows, then the top-scored target-like column
    (reusing the same outcome-scoring heuristic used for Auto Analyze's target
    detection — a business-outcome column deserves a headline KPI regardless of
    dataset), then other numeric columns, falling back to data-quality counts.

    A "Total X" KPI is only meaningful for a column role that's genuinely summable —
    money (a real total revenue/cost) or quantity (a real total units/orders). Summing
    an age, a price-per-unit, a rating, or a percentage produces a number with no
    business meaning (e.g. "Total Age: 48,213"), so those get an AVERAGE instead —
    and a percentage-shaped column is formatted as a percent, not a raw number."""
    kpis: list[dict] = [{"key": "total_rows", "label": "Total Rows", "value": float(len(df)), "format": "number"}]

    used_columns: set[str] = set()
    candidates = score_target_candidates(profile, description)
    if candidates:
        top = candidates[0]
        col = top["column"]
        if col in df.columns:
            used_columns.add(col)
            if top["problem_type_hint"] == "regression":
                series = pd.to_numeric(df[col], errors="coerce").dropna()
                role = (column_roles or {}).get(col, {}).get("role")
                kpis.append(
                    {
                        "key": f"avg_{col}",
                        "label": f"Average {_label(col)}",
                        "value": float(series.mean()) if len(series) else 0.0,
                        "format": "money" if role == "money" else "number",
                        "source_column": col,
                        "agg": "mean",
                    }
                )
            else:
                series = df[col].dropna().astype(str)
                if len(series) > 0:
                    vc = series.value_counts(normalize=True)
                    rate_class, is_positive = _pick_rate_class(vc)
                    label = f"{_label(col)} Rate" if is_positive else f"{_label(str(rate_class))} {_label(col)} Rate"
                    kpis.append(
                        {
                            "key": f"{col}_rate",
                            "label": label,
                            "value": float(vc.get(rate_class, 0.0)),
                            "format": "percent",
                            "source_column": col,
                            "agg": "rate",
                            "rate_value": str(rate_class),
                        }
                    )

    for col in _numeric_columns_from_profile(profile):
        if len(kpis) >= 4:
            break
        if col in used_columns or col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(series) == 0:
            continue
        used_columns.add(col)
        role = (column_roles or {}).get(col, {}).get("role")

        if role in ("money", "quantity"):
            kpis.append(
                {
                    "key": f"total_{col}",
                    "label": f"Total {_label(col)}",
                    "value": float(series.sum()),
                    "format": "money" if role == "money" else "number",
                    "source_column": col,
                    "agg": "sum",
                }
            )
        elif _looks_like_percentage(col, series):
            kpis.append(
                {
                    "key": f"avg_{col}",
                    "label": f"Average {_label(col)}",
                    "value": float(series.mean()) / 100,
                    "format": "percent",
                    "source_column": col,
                    "agg": "mean",
                }
            )
        else:
            # Not a real business total (an age, a rating, a per-unit price, ...) —
            # an average is the meaningful summary; a sum would have no business meaning.
            kpis.append(
                {
                    "key": f"avg_{col}",
                    "label": f"Average {_label(col)}",
                    "value": float(series.mean()),
                    "format": "number",
                    "source_column": col,
                    "agg": "mean",
                }
            )

    if len(kpis) < 4:
        kpis.append({"key": "missing_cells", "label": "Missing Cells", "value": float(int(df.isna().sum().sum())), "format": "number"})
    if len(kpis) < 4:
        kpis.append({"key": "duplicate_rows", "label": "Duplicate Rows", "value": float(int(df.duplicated().sum())), "format": "number"})

    return kpis[:4]


def _sparkline_series(df: pd.DataFrame, date_col: str, kpi: dict, points: int = SPARKLINE_POINTS) -> list[float]:
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    temp = df.assign(_parsed_date=parsed).dropna(subset=["_parsed_date"]).sort_values("_parsed_date")
    if len(temp) == 0:
        return []
    bucket_count = max(1, min(points, temp["_parsed_date"].nunique()))
    try:
        temp = temp.assign(_bucket=pd.qcut(temp["_parsed_date"].rank(method="first"), q=bucket_count, duplicates="drop"))
    except ValueError:
        return [round(_kpi_aggregate(temp, kpi), 4)]
    values = []
    for _, group in temp.groupby("_bucket", observed=True):
        values.append(round(_kpi_aggregate(group, kpi), 4))
    return values


def _apply_kpi_trend(df: pd.DataFrame, date_col: str | None, kpi: dict) -> None:
    """Adds a `trend` block (change vs the earlier half of the date range, plus a
    sparkline) to `kpi` IN PLACE — only possible when a date column exists."""
    if not date_col or date_col not in df.columns:
        return
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    valid_dates = parsed.dropna()
    if len(valid_dates) < 4:
        return
    median_date = valid_dates.median()
    earlier_df = df[parsed <= median_date]
    later_df = df[parsed > median_date]
    if len(earlier_df) == 0 or len(later_df) == 0:
        return

    earlier_value = _kpi_aggregate(earlier_df, kpi)
    later_value = _kpi_aggregate(later_df, kpi)
    change_pct = round((later_value - earlier_value) / abs(earlier_value) * 100, 1) if earlier_value != 0 else None

    kpi["trend"] = {
        "change_pct": change_pct,
        "previous_value": round(earlier_value, 4),
        "sparkline": _sparkline_series(df, date_col, kpi),
    }


def _select_donut_and_bar_charts(df: pd.DataFrame, profile: dict) -> tuple[list[dict], list[dict]]:
    """Two passes: first classify every categorical column as a donut or bar candidate
    purely by cardinality, THEN take the lowest-cardinality 3 for donuts (fewest slices
    reads best as a donut) — rather than a first-come, first-served single pass, which
    could shunt a genuinely low-cardinality column into the bar list just because 3
    earlier columns already filled the donut quota. Donut candidates beyond the top 3
    overflow into the bar pool instead of being dropped, so a dataset with many
    low-cardinality columns doesn't silently lose charts for the rest of them."""
    donut_candidates: list[tuple[int, dict]] = []
    bar_candidates: list[tuple[int, dict]] = []

    for col in _categorical_columns_from_profile(profile):
        if col not in df.columns:
            continue
        series = df[col].dropna().astype(str)
        if len(series) == 0:
            continue
        unique = int(series.nunique())
        total = len(series)
        vc = series.value_counts()
        items = [{"value": str(k), "count": int(v), "pct": round(float(v) / total * 100, 1)} for k, v in vc.items()]
        entry = {"column": col, "label": _label(col), "items": items}
        if unique <= DONUT_MAX_CARDINALITY:
            donut_candidates.append((unique, entry))
        elif unique <= BAR_MAX_CARDINALITY:
            bar_candidates.append((unique, entry))

    donut_candidates.sort(key=lambda pair: pair[0])
    donuts = [entry for _, entry in donut_candidates[:3]]
    bar_pool = donut_candidates[3:] + bar_candidates
    bar_pool.sort(key=lambda pair: pair[0])
    bars = [{**entry, "items": entry["items"][:10]} for _, entry in bar_pool[:3]]
    return donuts, bars


def _select_rating_breakdown(df: pd.DataFrame, profile: dict) -> dict | None:
    """A low-cardinality numeric column (a 1-5 rating, a small ordinal score...) gets a
    count+percentage breakdown, matching a "star rating" style panel — the first such
    column found (numeric columns are already profile-ordered by upload column order)."""
    for col in _numeric_columns_from_profile(profile):
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        unique = series.nunique()
        if not (RATING_MIN_CARDINALITY <= unique <= RATING_MAX_CARDINALITY):
            continue
        total = len(series)
        if total == 0:
            continue
        vc = series.value_counts().sort_index(ascending=False)
        breakdown = []
        for value, count in vc.items():
            display_value = int(value) if float(value).is_integer() else round(float(value), 2)
            breakdown.append({"value": display_value, "count": int(count), "pct": round(float(count) / total * 100, 1)})
        return {
            "column": col,
            "label": _label(col),
            "average": round(float(series.mean()), 2),
            "min": float(series.min()),
            "max": float(series.max()),
            "breakdown": breakdown,
        }
    return None


def _select_time_trend(
    df: pd.DataFrame, profile: dict, description: str | None = None, column_roles: dict[str, dict] | None = None
) -> dict | None:
    date_cols = _datetime_columns_from_profile(profile)
    if not date_cols:
        return None
    date_col = date_cols[0]
    if date_col not in df.columns:
        return None
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    # Excludes invalid/implausible dates (see flag_invalid_dates) so a single planted
    # outlier date doesn't stretch the trend chart's x-axis across years of empty buckets.
    invalid = flag_invalid_dates(df[date_col])
    temp = df.assign(_parsed_date=parsed)[~invalid].dropna(subset=["_parsed_date"])
    if len(temp) == 0:
        return None

    grouped = temp.set_index("_parsed_date").resample("ME")
    row_counts = grouped.size()
    if len(row_counts) == 0:
        return None
    labels = [str(idx.date()) for idx in row_counts.index]
    series = [{"name": "Row Count", "values": [int(v) for v in row_counts.values]}]

    # Prefer the business-outcome/target column (when one is confidently identifiable) for
    # the trend's secondary series and peak-month finding — a generic "first numeric
    # column in upload order" would otherwise chart/narrate whatever happened to be
    # uploaded first, not the metric that actually matters (e.g. sales_amount, not an
    # arbitrary adjacent numeric column).
    numeric_cols = [c for c in _numeric_columns_from_profile(profile) if c in temp.columns]
    candidates = score_target_candidates(profile, description)
    metric_col = None
    if candidates and candidates[0]["column"] in numeric_cols:
        metric_col = candidates[0]["column"]
    elif numeric_cols:
        metric_col = numeric_cols[0]

    peak_month = None
    if metric_col:
        metric_mean = grouped[metric_col].mean()
        series.append(
            {
                "name": f"Average {_label(metric_col)}",
                "values": [round(float(v), 4) if pd.notna(v) else 0.0 for v in metric_mean.values],
            }
        )
        # The PEAK month is about total activity in that month (a real, sum-based
        # business peak, e.g. "November-December is when total sales are highest"), not
        # the per-row average — a month with fewer, larger orders could have a high mean
        # but low total, which isn't what "peak month" means in a revenue/business sense.
        monthly_sum = grouped[metric_col].sum()
        if len(monthly_sum) > 0 and monthly_sum.notna().any():
            peak_idx = monthly_sum.idxmax()
            role = (column_roles or {}).get(metric_col, {}).get("role")
            peak_month = {
                "column": metric_col,
                "label": _label(metric_col),
                "month": peak_idx.strftime("%B %Y"),
                "value": round(float(monthly_sum.loc[peak_idx]), 2),
                "format": "money" if role == "money" else "number",
            }

    return {"date_column": date_col, "labels": labels, "series": series, "peak_month": peak_month}


def _build_filter_options(full_df: pd.DataFrame, profile: dict) -> dict:
    """Built from the UNFILTERED dataset — filter dropdown options don't shrink as the
    user narrows down results, matching standard BI dashboard behavior."""
    categorical_options: dict[str, list[str]] = {}
    for col in _categorical_columns_from_profile(profile):
        if col not in full_df.columns:
            continue
        unique_values = full_df[col].dropna().astype(str).unique().tolist()
        if 0 < len(unique_values) <= FILTER_OPTIONS_MAX_CARDINALITY:
            categorical_options[col] = sorted(unique_values)

    date_ranges: dict[str, dict[str, str]] = {}
    for col in _datetime_columns_from_profile(profile):
        if col not in full_df.columns:
            continue
        parsed = pd.to_datetime(full_df[col], errors="coerce").dropna()
        if len(parsed) > 0:
            date_ranges[col] = {"min": str(parsed.min().date()), "max": str(parsed.max().date())}

    return {"categorical": categorical_options, "date_ranges": date_ranges}


def run_filtered_eda(
    db: Session,
    dataset: Dataset,
    categorical_filters: dict[str, list[str]] | None = None,
    date_filters: dict[str, dict[str, str]] | None = None,
) -> dict:
    """The BI-dashboard EDA endpoint's engine: aggregated JSON only (never raw rows),
    auto-selecting KPIs and chart types purely from column types/cardinality so it works
    unchanged for any dataset. Cached briefly per (dataset, cleaning version, filters)
    since repeated identical filter combinations are common during interactive use."""
    cache_key = _eda_cache_key(dataset.id, dataset.cleaning_version, categorical_filters, date_filters)
    cached = _FILTERED_EDA_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    full_df = load_dataframe(dataset)
    profile = dataset.profile_json or {}
    description = dataset.project.description if dataset.project else None

    filtered_df = _apply_filters(full_df, categorical_filters, date_filters)

    column_roles = classify_columns(full_df, profile, use_gemini=False)
    kpis = _select_kpis(filtered_df, profile, description, column_roles)
    date_cols = _datetime_columns_from_profile(profile)
    primary_date_col = date_cols[0] if date_cols else None
    for kpi in kpis:
        _apply_kpi_trend(filtered_df, primary_date_col, kpi)

    donut_charts, bar_charts = _select_donut_and_bar_charts(filtered_df, profile)

    result = {
        "summary": {
            "total_rows": int(len(full_df)),
            "filtered_rows": int(len(filtered_df)),
            "columns": int(full_df.shape[1]),
            "is_filtered": bool(categorical_filters or date_filters),
        },
        "kpis": kpis,
        "donut_charts": donut_charts,
        "bar_charts": bar_charts,
        "rating_breakdown": _select_rating_breakdown(filtered_df, profile),
        "time_trend": _select_time_trend(filtered_df, profile, description, column_roles),
        "filter_options": _build_filter_options(full_df, profile),
    }

    _FILTERED_EDA_CACHE[cache_key] = (time.time(), result)
    return result
