"""Phase 4 — the single shared analysis planner. Computes, in one place, which analyses
a dataset supports (supervised prediction, segmentation, and — for transaction-log
datasets — revenue analytics/RFM/return prediction/forecasting) instead of that decision
being split between auto_analyze_service's inline branching and
transaction_analysis_service's own separate, transaction-log-only planner. Every Auto
Analyze run now computes and stores ONE plan (job.result_json["plan"]), not just
transaction-log ones — even though the actual pipeline still only acts on one branch of
it (a transaction-log dataset has never ALSO been scored for a supervised target;
preserving that existing, already-validated mutual exclusivity rather than redesigning it
is a deliberate, in-scope-for-this-phase choice, not an oversight), the full plan is
visible for transparency: which analyses were considered, WHY (a "reason" string per
entry), and how confident the estimate is (a "confidence" level: high/medium/low/none).

Every plan is shown to the user for confirmation before anything runs — never silently
auto-applied — via job.status == AWAITING_PLAN_CONFIRMATION (auto_analyze_service). This
was already true for transaction-log and supervised-target plans; the general/segmentation
path used to run straight through with no confirmation step at all, which meant a
LOW-confidence segmentation call could execute without the user ever seeing it — fixed
alongside this file, see docs/PROGRESS.md Finding 20. The user can also always override the
plan by supplying a target_column when confirming (auto_analyze_service.confirm_plan_and_resume),
routing to supervised training on that column instead of whatever the plan proposed.

A plan entry's "viable" is an ESTIMATE from cheap, pre-execution signals (row counts,
column roles, candidate scores) — the real, authoritative answer is always whatever the
actual execution function (ml_service.train_models, ml_service.run_clustering_analysis,
transaction_analysis_service.run_full_transaction_analysis) determines when it actually
runs; this planner never claims more certainty than that, and never overrides what real
execution decides.
"""

import pandas as pd

from app.services import transaction_analysis_service

MIN_ROWS_FOR_SEGMENTATION = 20
MIN_NUMERIC_COLUMNS_FOR_SEGMENTATION = 2

# Confidence bands for a target-candidate score — calibrated against real scores observed
# across this project's 6 benchmark datasets (dataset_service.score_target_candidates): a
# bare name-hint match alone scores 3-5 (TARGET_HINT_TIER1/2/3); combined with a genuine
# cardinality-shape match (binary/low-cardinality/high-cardinality-numeric), 5-8.
CONFIDENCE_SCORE_HIGH = 5.0
CONFIDENCE_SCORE_MEDIUM = 3.0
# A runner-up candidate scoring within this margin of the top one signals real ambiguity
# (the name/shape evidence doesn't clearly favor one column), so the top pick's confidence
# is downgraded one band even if its raw score alone would read as "high".
CONFIDENCE_AMBIGUITY_MARGIN = 1.0


def _downgrade(level: str) -> str:
    return {"high": "medium", "medium": "low", "low": "low", "none": "none"}[level]


def _score_confidence(score: float) -> str:
    if score >= CONFIDENCE_SCORE_HIGH:
        return "high"
    if score >= CONFIDENCE_SCORE_MEDIUM:
        return "medium"
    return "low"


def _segmentation_entry(df: pd.DataFrame, profile: dict) -> dict:
    numeric_cols = [
        c["name"] for c in profile.get("columns", []) if c.get("is_numeric") and not c.get("is_id_like")
    ]
    rows = len(df)
    viable = rows >= MIN_ROWS_FOR_SEGMENTATION and len(numeric_cols) >= MIN_NUMERIC_COLUMNS_FOR_SEGMENTATION

    if not viable:
        confidence, reason = "none", (
            f"Only {len(numeric_cols)} usable numeric column(s) and {rows} row(s) — needs at least "
            f"{MIN_NUMERIC_COLUMNS_FOR_SEGMENTATION} columns and {MIN_ROWS_FOR_SEGMENTATION} rows to cluster at all."
        )
    elif len(numeric_cols) >= 5 and rows >= 100:
        confidence, reason = "high", (
            f"{len(numeric_cols)} numeric behavioral column(s) and {rows} rows — comfortably above the "
            "minimum needed for a meaningful cluster structure."
        )
    else:
        confidence, reason = "medium", (
            f"{len(numeric_cols)} numeric behavioral column(s) and {rows} rows — meets the minimum to "
            "attempt clustering, but is a thinner basis than ideal."
        )

    return {
        "key": "segmentation",
        "label": "Segmentation (clustering)",
        "description": (
            f"Groups rows into behaviorally similar clusters using {len(numeric_cols)} numeric column(s) "
            "(demographics describe the resulting segments, they don't form them), with k chosen from "
            "silhouette, GMM BIC, and bootstrap stability together."
        ),
        "viable": viable,
        "confidence": confidence,
        "reason": reason,
    }


def _supervised_entry(target_candidates: list[dict]) -> dict:
    if not target_candidates:
        return {
            "key": "supervised_prediction",
            "label": "Predictive Modeling",
            "description": (
                "No column showed strong enough evidence (a name/description match to a business outcome, "
                "or a real relationship with one) to be a confident prediction target."
            ),
            "viable": False,
            "confidence": "none",
            "reason": "No column qualified as a target candidate at all.",
        }

    top = target_candidates[0]
    confidence = _score_confidence(top["score"])
    reason = top["reason"]
    if len(target_candidates) >= 2:
        runner_up = target_candidates[1]
        if (top["score"] - runner_up["score"]) < CONFIDENCE_AMBIGUITY_MARGIN:
            confidence = _downgrade(confidence)
            reason += (
                f"; runner-up '{runner_up['column']}' scored close behind ({runner_up['score']} vs "
                f"{top['score']}), so this suggestion is less clear-cut than the raw score alone implies."
            )

    return {
        "key": "supervised_prediction",
        "label": "Predictive Modeling",
        "description": f"Trains a model to predict '{top['column']}' ({top['problem_type_hint']}) from the other columns.",
        "viable": True,
        "confidence": confidence,
        "reason": reason,
        "suggested_target": top["column"],
    }


def build_analysis_plan(
    df: pd.DataFrame,
    column_roles: dict[str, dict],
    dataset_type_info: dict,
    profile: dict,
    target_candidates: list[dict],
) -> dict:
    """The single entry point every Auto Analyze run calls. For a transaction_log
    dataset, delegates the transaction-specific entries (revenue/RFM/returns/forecast,
    each already carrying its own confidence/reason) to transaction_analysis_service's
    already-tested viability math rather than duplicating it. Also computes an overall
    plan["confidence"] — the confidence of the PRIMARY analysis this plan leads with
    (revenue analytics for a transaction log, since dataset-type detection already
    requires real customer/money/timestamp evidence to reach this branch at all; the
    supervised or segmentation entry's own confidence otherwise) — so the caller can
    decide whether extra scrutiny is warranted before confirming."""
    if dataset_type_info["type"] == "transaction_log":
        tx_plan = transaction_analysis_service.build_analysis_plan(column_roles, dataset_type_info, df)
        primary = next((a for a in tx_plan["analyses"] if a["key"] == "revenue_analytics"), None)
        return {
            "dataset_type": dataset_type_info["type"],
            "analyses": tx_plan["analyses"],
            "confidence": (primary or {}).get("confidence", "high"),
        }

    supervised = _supervised_entry(target_candidates)
    segmentation = _segmentation_entry(df, profile)
    primary = supervised if supervised["viable"] else segmentation
    return {
        "dataset_type": dataset_type_info["type"],
        "analyses": [supervised, segmentation],
        "confidence": primary["confidence"],
    }
