"""Aggregates a transaction log into one row per identified customer — Recency,
Frequency, Monetary (RFM) value, favorite branch/category, and loyalty usage — then
assigns a plain-language RFM segment. Walk-in/unidentified transactions (missing
customer_id, labeled WALK_IN_LABEL by cleaning_service) are EXCLUDED here: there is no
customer to attribute them to, and the spec is explicit that identifiers are never
fabricated to force them into a customer-level view."""

import pandas as pd

from app.services.cleaning_service import WALK_IN_LABEL

# "At Risk" was renamed to "Slipping Away" (a recognized RFM/lifecycle-marketing term) —
# a report that also runs return_prediction_service shows a DIFFERENT, ML-predicted
# "At risk of not returning" group computed from a trained classifier on recent behavior,
# not this RFM heuristic on historical recency/frequency; the near-identical wording
# previously made two genuinely different, independently-computed groups look like the
# same thing.
RFM_SEGMENTS = {
    "champions": "Champions",
    "loyal": "Loyal Customers",
    "new": "New Customers",
    "at_risk": "Slipping Away",
    "lost": "Lost",
    "needs_attention": "Needs Attention",
}


def pick_column(column_roles: dict[str, dict], role: str, name_prefer: tuple[str, ...] = ()) -> str | None:
    candidates = [n for n, r in column_roles.items() if r["role"] == role]
    if not candidates:
        return None
    for prefer in name_prefer:
        for c in candidates:
            if prefer in c.lower():
                return c
    return candidates[0]


def build_customer_table(
    df: pd.DataFrame,
    column_roles: dict[str, dict],
    as_of_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """One row per identified customer. `df` should already be cleaned and have invalid
    dates excluded by the caller (see cleaning_service.flag_invalid_dates)."""
    customer_col = pick_column(column_roles, "customer_id")
    timestamp_col = pick_column(column_roles, "timestamp")
    money_col = pick_column(column_roles, "money", name_prefer=("total",))
    category_cols = [n for n, r in column_roles.items() if r["role"] == "category"]
    branch_col = next((c for c in category_cols if "branch" in c.lower()), None)
    item_category_col = next((c for c in category_cols if c.lower() == "category"), None) or next(
        (c for c in category_cols if "categor" in c.lower() and c != branch_col), None
    )
    loyalty_col = next((c for c in df.columns if "loyalty" in str(c).lower()), None)

    if not customer_col or not timestamp_col or not money_col:
        raise ValueError("Cannot build a customer table without customer_id, timestamp, and money columns.")

    work = df[df[customer_col].notna() & (df[customer_col] != WALK_IN_LABEL)].copy()
    work[timestamp_col] = pd.to_datetime(work[timestamp_col], errors="coerce")
    work = work.dropna(subset=[timestamp_col])
    if work.empty:
        return pd.DataFrame(
            columns=["customer_id", "recency_days", "frequency", "monetary", "refund_total", "favorite_branch", "favorite_category", "loyalty_points_used", "first_seen", "last_seen"]
        )

    reference = as_of_date or work[timestamp_col].max()

    grouped = work.groupby(customer_col)
    rows = []
    for customer_id, g in grouped:
        last_seen = g[timestamp_col].max()
        first_seen = g[timestamp_col].min()
        recency_days = int((reference - last_seen).days)
        frequency = int(g[timestamp_col].nunique())  # distinct visit occasions, not line items
        monetary = float(g[money_col].sum())
        refund_total = float(g.loc[g[money_col] < 0, money_col].sum())
        favorite_branch = g[branch_col].mode().iloc[0] if branch_col and len(g[branch_col].mode()) else None
        favorite_category = (
            g[item_category_col].mode().iloc[0] if item_category_col and len(g[item_category_col].mode()) else None
        )
        loyalty_used = float(g[loyalty_col].sum()) if loyalty_col else 0.0
        rows.append(
            {
                "customer_id": customer_id,
                "recency_days": recency_days,
                "frequency": frequency,
                "monetary": round(monetary, 2),
                "refund_total": round(refund_total, 2),
                "favorite_branch": favorite_branch,
                "favorite_category": favorite_category,
                "loyalty_points_used": loyalty_used,
                "first_seen": first_seen,
                "last_seen": last_seen,
            }
        )
    return pd.DataFrame(rows)


def _quantile_score(series: pd.Series, ascending: bool, bins: int = 5) -> pd.Series:
    """Scores 1 (worst) to `bins` (best). Falls back to a coarser bin count when there
    aren't enough distinct values for `bins` quantiles (common with small customer
    counts) rather than raising."""
    ranked = series.rank(method="first", ascending=ascending)
    for n_bins in range(bins, 1, -1):
        try:
            return pd.qcut(ranked, n_bins, labels=list(range(1, n_bins + 1))).astype(int)
        except ValueError:
            continue
    return pd.Series(1, index=series.index)


def _segment_row(r_score: int, f_score: int, max_score: int) -> str:
    high = max_score - max(1, max_score // 5) + 1  # top ~20% band, scaled to whatever bin count was used
    low = max(1, max_score // 5)
    if r_score >= high and f_score >= high:
        return RFM_SEGMENTS["champions"]
    if f_score >= high and r_score >= (max_score // 2):
        return RFM_SEGMENTS["loyal"]
    if r_score >= high and f_score <= low:
        return RFM_SEGMENTS["new"]
    if r_score <= low and f_score >= (max_score // 2):
        return RFM_SEGMENTS["at_risk"]
    if r_score <= low and f_score <= low:
        return RFM_SEGMENTS["lost"]
    return RFM_SEGMENTS["needs_attention"]


def segment_rfm(customer_df: pd.DataFrame) -> pd.DataFrame:
    """Adds r_score/f_score/m_score (1-5, or fewer bins if the customer count is small)
    and a plain-language `rfm_segment` column, using a standard, widely-published RFM
    segment scheme (Champions/Loyal/New/At Risk/Lost/Needs Attention) rather than an
    ad-hoc one."""
    if customer_df.empty:
        return customer_df.assign(r_score=[], f_score=[], m_score=[], rfm_segment=[])

    out = customer_df.copy()
    out["r_score"] = _quantile_score(out["recency_days"], ascending=False)  # low recency_days = recent = high score
    out["f_score"] = _quantile_score(out["frequency"], ascending=True)
    out["m_score"] = _quantile_score(out["monetary"], ascending=True)
    max_score = int(out["r_score"].max())
    out["rfm_segment"] = [
        _segment_row(int(r), int(f), max_score) for r, f in zip(out["r_score"], out["f_score"])
    ]
    return out


def summarize_segments(segmented_df: pd.DataFrame) -> list[dict]:
    if segmented_df.empty:
        return []
    summary = []
    for segment, g in segmented_df.groupby("rfm_segment"):
        summary.append(
            {
                "segment": segment,
                "customer_count": int(len(g)),
                "pct_of_customers": round(len(g) / len(segmented_df) * 100, 1),
                "avg_monetary": round(float(g["monetary"].mean()), 2),
                "avg_frequency": round(float(g["frequency"].mean()), 1),
                "avg_recency_days": round(float(g["recency_days"].mean()), 1),
                "total_monetary": round(float(g["monetary"].sum()), 2),
            }
        )
    summary.sort(key=lambda s: s["total_monetary"], reverse=True)
    return summary
