"""Orchestrates the full transaction-log analysis pipeline: revenue analytics, customer/
RFM segmentation, return prediction, and revenue forecasting — the four analyses the spec
requires for a detected "transaction_log" dataset. Each piece degrades independently and
honestly (e.g. forecasting returns None rather than a fabricated number when there isn't
enough history) rather than the whole pipeline failing because one part isn't viable."""

import pandas as pd

from app.services import customer_analytics_service as cas
from app.services import forecast_service
from app.services import return_prediction_service as rps
from app.services.cleaning_service import WALK_IN_LABEL, flag_invalid_dates
from app.services.customer_analytics_service import pick_column
from app.utils.formatting import detect_currency_from_column_name, format_money


def build_analysis_plan(column_roles: dict[str, dict], dataset_type_info: dict, df: pd.DataFrame) -> dict:
    """The plan shown to the user BEFORE anything runs — spec: 'show the plan to the user
    to confirm before running'. Includes a real viability check for each analysis (not
    just "always include everything") so the plan honestly reflects what can actually be
    computed from this dataset, plus a confidence level (how comfortably the real numbers
    clear each viability threshold, not just whether they technically do) and a reason
    string explaining both."""
    analyses = []
    if dataset_type_info["type"] != "transaction_log":
        return {"dataset_type": dataset_type_info["type"], "analyses": []}

    analyses.append(
        {
            "key": "revenue_analytics",
            "label": "Revenue Analytics",
            "description": "Total revenue, refunds, average order value, and revenue breakdowns by month, "
            "category, item, branch, payment method, hour of day, and day of week.",
            "viable": True,
            "confidence": "high",
            "reason": "Needs only the monetary column already required to classify this as a transaction log.",
        }
    )

    timestamp_col = pick_column(column_roles, "timestamp")
    customer_col = pick_column(column_roles, "customer_id")
    identified = df[customer_col].notna() & (df[customer_col] != WALK_IN_LABEL) if customer_col else pd.Series(False, index=df.index)
    n_identified_customers = int(df.loc[identified, customer_col].nunique()) if customer_col else 0

    rfm_viable = n_identified_customers >= 10
    rfm_confidence = "high" if n_identified_customers >= 50 else ("medium" if rfm_viable else "none")
    analyses.append(
        {
            "key": "customer_rfm",
            "label": "Customer Analytics & RFM Segmentation",
            "description": f"Aggregates {n_identified_customers} identified customer(s) (walk-ins excluded) into "
            "Recency/Frequency/Monetary segments (Champions, At Risk, Lost, ...).",
            "viable": rfm_viable,
            "confidence": rfm_confidence,
            "reason": f"{n_identified_customers} identified customer(s) (walk-ins excluded); needs at least 10.",
        }
    )

    # Excludes invalid/implausible dates (unparseable, future, or far outside the bulk of
    # the column's own range — see flag_invalid_dates) BEFORE computing span/coverage, the
    # same filtering run_full_transaction_analysis applies at actual execution time. Without
    # this, a single planted bad date (e.g. a stray 2099 timestamp among 2025/2026 real
    # ones) inflates span_days/weeks_covered by decades — still numerically "viable" either
    # way, but a wildly wrong number to show the user in a confidence/reason string.
    parsed_raw = pd.to_datetime(df[timestamp_col], errors="coerce") if timestamp_col else pd.Series(dtype="datetime64[ns]")
    parsed = parsed_raw[~flag_invalid_dates(df[timestamp_col])] if timestamp_col else parsed_raw
    span_days = int((parsed.max() - parsed.min()).days) if parsed.notna().any() else 0
    return_viable = n_identified_customers >= 30 and span_days >= (rps.RETURN_WINDOW_DAYS * 2)
    return_confidence = (
        "high"
        if n_identified_customers >= 100 and span_days >= (rps.RETURN_WINDOW_DAYS * 3)
        else ("medium" if return_viable else "none")
    )
    analyses.append(
        {
            "key": "return_prediction",
            "label": "Customer Return Prediction",
            "description": f"Predicts which of the {n_identified_customers} identified customers will purchase "
            f"again within the next {rps.RETURN_WINDOW_DAYS} days, using a time-based train/predict split "
            "(never trained on future data).",
            "viable": return_viable,
            "confidence": return_confidence,
            "reason": (
                f"{n_identified_customers} identified customer(s) and {span_days} day(s) of history; needs at "
                f"least 30 customers and {rps.RETURN_WINDOW_DAYS * 2} days to establish a time-based split."
            ),
        }
    )

    money_col = pick_column(column_roles, "money", name_prefer=("total",))
    weeks_covered = int((parsed.max() - parsed.min()).days // 7) if timestamp_col and parsed.notna().any() else 0
    forecast_viable = bool(money_col) and weeks_covered >= 12
    forecast_confidence = "high" if weeks_covered >= 26 else ("medium" if forecast_viable else "none")
    analyses.append(
        {
            "key": "sales_forecasting",
            "label": "Sales Forecasting",
            "description": "Forecasts weekly revenue for the next ~13 weeks (shown aggregated to monthly), "
            "comparing several methods via a rolling backtest and picking the best-performing one.",
            "viable": forecast_viable,
            "confidence": forecast_confidence,
            "reason": f"{weeks_covered} week(s) of history; needs at least 12 to backtest a forecast reliably.",
        }
    )

    return {"dataset_type": dataset_type_info["type"], "analyses": analyses}


def _extract_hour_and_weekday(df: pd.DataFrame, timestamp_col: str) -> pd.DataFrame:
    out = df.copy()
    parsed = pd.to_datetime(out[timestamp_col], errors="coerce")
    out["_hour"] = parsed.dt.hour
    out["_weekday"] = parsed.dt.day_name()
    out["_month"] = parsed.dt.to_period("M")
    return out


def run_revenue_analytics(df: pd.DataFrame, column_roles: dict[str, dict]) -> dict:
    money_col = pick_column(column_roles, "money", name_prefer=("total",))
    timestamp_col = pick_column(column_roles, "timestamp")
    category_cols = [n for n, r in column_roles.items() if r["role"] == "category"]
    branch_col = next((c for c in category_cols if "branch" in c.lower()), None)
    item_col = next((c for c in df.columns if str(c).lower() == "item"), None)
    item_category_col = next((c for c in category_cols if c.lower() == "category"), None)
    payment_col = next((c for c in category_cols if "payment" in c.lower()), None)

    work = _extract_hour_and_weekday(df, timestamp_col) if timestamp_col else df.copy()

    total_revenue = float(work.loc[work[money_col] > 0, money_col].sum())
    refund_total = float(-work.loc[work[money_col] < 0, money_col].sum())
    refund_count = int((work[money_col] < 0).sum())
    order_count = int(len(work))
    avg_order_value = round(total_revenue / max(order_count - refund_count, 1), 2)
    refund_rate = round(refund_count / max(order_count, 1) * 100, 2)

    def _breakdown(group_col: str | None, top_n: int = 20) -> list[dict]:
        if not group_col or group_col not in work.columns:
            return []
        g = work.groupby(group_col)[money_col].sum().sort_values(ascending=False).head(top_n)
        return [{"label": str(k), "revenue": round(float(v), 2)} for k, v in g.items()]

    revenue_by_month = []
    if timestamp_col:
        g = work.groupby("_month")[money_col].sum().sort_index()
        revenue_by_month = [{"month": str(k), "revenue": round(float(v), 2)} for k, v in g.items()]

    revenue_by_hour = []
    if timestamp_col:
        g = work.groupby("_hour")[money_col].sum().sort_index()
        revenue_by_hour = [{"hour": int(k), "revenue": round(float(v), 2)} for k, v in g.items() if pd.notna(k)]

    revenue_by_weekday = []
    if timestamp_col:
        weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        g = work.groupby("_weekday")[money_col].sum()
        revenue_by_weekday = [{"day": d, "revenue": round(float(g.get(d, 0.0)), 2)} for d in weekday_order if d in g.index]

    return {
        "total_revenue": round(total_revenue, 2),
        "refund_total": round(refund_total, 2),
        "refund_count": refund_count,
        "refund_rate_pct": refund_rate,
        "order_count": order_count,
        "average_order_value": avg_order_value,
        "revenue_by_month": revenue_by_month,
        "revenue_by_category": _breakdown(item_category_col),
        "revenue_by_item": _breakdown(item_col),
        "revenue_by_branch": _breakdown(branch_col),
        "revenue_by_payment_method": _breakdown(payment_col),
        "revenue_by_hour": revenue_by_hour,
        "revenue_by_weekday": revenue_by_weekday,
    }


def run_full_transaction_analysis(df: pd.DataFrame, column_roles: dict[str, dict], plan: dict) -> dict:
    """Executes whichever analyses in `plan` are marked viable. `df` must already be
    cleaned (refunds preserved, missing customer_id labeled WALK_IN_LABEL) with invalid
    dates already excluded (see cleaning_service.flag_invalid_dates) by the caller."""
    viable_keys = {a["key"] for a in plan["analyses"] if a["viable"]}
    result: dict = {}

    if "revenue_analytics" in viable_keys:
        result["revenue_analytics"] = run_revenue_analytics(df, column_roles)

    customer_table = None
    if "customer_rfm" in viable_keys:
        customer_table = cas.build_customer_table(df, column_roles)
        segmented = cas.segment_rfm(customer_table)
        result["customer_rfm"] = {
            "total_identified_customers": int(len(segmented)),
            "segments": cas.summarize_segments(segmented),
        }

    if "return_prediction" in viable_keys:
        result["return_prediction"] = rps.run_return_prediction(df, column_roles)

    if "sales_forecasting" in viable_keys:
        timestamp_col = pick_column(column_roles, "timestamp")
        money_col = pick_column(column_roles, "money", name_prefer=("total",))
        customer_col = pick_column(column_roles, "customer_id")
        result["sales_forecasting"] = forecast_service.forecast_weekly_revenue(
            df, timestamp_col, money_col, customer_col, periods_weeks=13
        )

    money_col = pick_column(column_roles, "money", name_prefer=("total",))
    currency = detect_currency_from_column_name(money_col)
    result["recommendations"] = generate_rule_based_recommendations(result, customer_table, currency)
    return result


def _peak_hour_range(revenue_by_hour: list[dict], threshold_fraction: float = 0.7) -> tuple[str, float]:
    """The contiguous window of hours around the single peak hour whose revenue is within
    `threshold_fraction` of the peak — a natural "peak window" (e.g. "7 PM-11 PM") rather
    than one artificially precise hour that overstates how sharp a real peak actually is."""
    by_hour = {r["hour"]: r["revenue"] for r in revenue_by_hour}
    peak_hour = max(by_hour, key=by_hour.get)
    peak_value = by_hour[peak_hour]
    threshold = peak_value * threshold_fraction

    start = end = peak_hour
    while (start - 1) in by_hour and by_hour[start - 1] >= threshold:
        start -= 1
    while (end + 1) in by_hour and by_hour[end + 1] >= threshold:
        end += 1

    def _fmt(h: int) -> str:
        period = "AM" if h < 12 else "PM"
        display_h = h % 12
        display_h = 12 if display_h == 0 else display_h
        return f"{display_h} {period}"

    label = _fmt(start) if start == end else f"{_fmt(start)}–{_fmt(end)}"
    return label, peak_value


def generate_rule_based_recommendations(
    analysis: dict, customer_table: pd.DataFrame | None, currency: str | None = None
) -> list[str]:
    """Deterministic, non-AI recommendations from verified findings already computed
    above — always available regardless of Gemini's status, since none of this depends
    on it. Every sentence cites a real, already-computed number; nothing here is
    templated boilerplate unrelated to this dataset."""
    recs: list[str] = []

    revenue = analysis.get("revenue_analytics")
    if revenue:
        if revenue.get("revenue_by_category"):
            top = revenue["revenue_by_category"][0]
            recs.append(
                f"'{top['label']}' is the top revenue category at {format_money(top['revenue'], currency)} — "
                "consider prioritizing inventory, staffing, and promotions around it."
            )
        if revenue.get("revenue_by_hour"):
            peak_label, peak_value = _peak_hour_range(revenue["revenue_by_hour"])
            recs.append(
                f"Peak revenue window is {peak_label} ({format_money(peak_value, currency)} in that single "
                "busiest hour) — align staffing levels and time-limited promotions with this window."
            )
        refund_rate = revenue.get("refund_rate_pct")
        if refund_rate is not None:
            if refund_rate >= 2.0:
                recs.append(
                    f"Refund rate is {refund_rate}% of transactions — elevated enough to warrant reviewing "
                    "which items/branches drive it."
                )
            else:
                recs.append(
                    f"Refund rate is {refund_rate}% of transactions — low, with no sign of a widespread "
                    "product or service-quality issue."
                )

    rfm = analysis.get("customer_rfm")
    if rfm and rfm.get("segments"):
        segments = rfm["segments"]
        at_risk_segments = (cas.RFM_SEGMENTS["at_risk"], cas.RFM_SEGMENTS["lost"])
        at_risk_count = sum(s["customer_count"] for s in segments if s["segment"] in at_risk_segments)
        at_risk_value = sum(s["total_monetary"] for s in segments if s["segment"] in at_risk_segments)
        total_customers = rfm["total_identified_customers"]
        if at_risk_count and total_customers:
            pct = round(at_risk_count / total_customers * 100, 1)
            recs.append(
                f"{at_risk_count} customers ({pct}% of identified customers) are in the '{at_risk_segments[0]}' or "
                f"'{at_risk_segments[1]}' RFM segments (historical purchase pattern, not a return-likelihood "
                f"prediction), representing {format_money(at_risk_value, currency)} in historical spend — a "
                "targeted re-engagement offer to this group could recover meaningful revenue."
            )

    return_prediction = analysis.get("return_prediction")
    if return_prediction and customer_table is not None and not customer_table.empty:
        loyalty_col = "loyalty_points_used"
        if loyalty_col in customer_table.columns:
            preds = pd.DataFrame(return_prediction["predictions"])[["customer_id", "return_probability"]]
            merged = customer_table[["customer_id", loyalty_col]].merge(preds, on="customer_id", how="inner")
            if not merged.empty:
                is_member = merged[loyalty_col] > 0
                if is_member.any() and (~is_member).any():
                    member_rate = round(float(merged.loc[is_member, "return_probability"].mean()) * 100, 1)
                    non_member_rate = round(float(merged.loc[~is_member, "return_probability"].mean()) * 100, 1)
                    gap = round(member_rate - non_member_rate, 1)
                    if abs(gap) >= 1:
                        higher = "loyalty-program members" if gap > 0 else "non-members"
                        recs.append(
                            f"Predicted return likelihood is {member_rate}% for customers who have redeemed "
                            f"loyalty points vs. {non_member_rate}% for those who haven't — {higher} show a "
                            f"{abs(gap)} point higher predicted return rate, suggesting the loyalty program "
                            "is genuinely associated with retention."
                        )
                    else:
                        recs.append(
                            f"Predicted return likelihood is nearly identical for loyalty-program members "
                            f"({member_rate}%) and non-members ({non_member_rate}%) — the loyalty program does "
                            "not show a measurable retention effect in this data."
                        )

    return recs
