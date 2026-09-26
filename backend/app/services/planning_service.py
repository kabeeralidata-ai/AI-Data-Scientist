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
visible for transparency: which analyses were considered and why each was or wasn't
viable.

A plan entry's "viable": True is an ESTIMATE from cheap, pre-execution signals (row
counts, column roles, candidate scores) — the real, authoritative answer is always
whatever the actual execution function (ml_service.train_models,
ml_service.run_clustering_analysis, transaction_analysis_service.run_full_transaction_analysis)
determines when it actually runs; this planner never claims more certainty than that, and
never overrides what real execution decides.
"""

import pandas as pd

from app.services import transaction_analysis_service

MIN_ROWS_FOR_SEGMENTATION = 20
MIN_NUMERIC_COLUMNS_FOR_SEGMENTATION = 2


def _segmentation_entry(df: pd.DataFrame, profile: dict) -> dict:
    numeric_cols = [
        c["name"] for c in profile.get("columns", []) if c.get("is_numeric") and not c.get("is_id_like")
    ]
    viable = len(df) >= MIN_ROWS_FOR_SEGMENTATION and len(numeric_cols) >= MIN_NUMERIC_COLUMNS_FOR_SEGMENTATION
    return {
        "key": "segmentation",
        "label": "Segmentation (clustering)",
        "description": (
            f"Groups rows into behaviorally similar clusters using {len(numeric_cols)} numeric column(s) "
            "(demographics describe the resulting segments, they don't form them), with k chosen from "
            "silhouette, GMM BIC, and bootstrap stability together."
        ),
        "viable": viable,
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
        }
    top = target_candidates[0]
    return {
        "key": "supervised_prediction",
        "label": "Predictive Modeling",
        "description": f"Trains a model to predict '{top['column']}' ({top['problem_type_hint']}) from the other columns.",
        "viable": True,
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
    dataset, delegates the transaction-specific entries (revenue/RFM/returns/forecast) to
    transaction_analysis_service's already-tested viability math rather than duplicating
    it."""
    if dataset_type_info["type"] == "transaction_log":
        tx_plan = transaction_analysis_service.build_analysis_plan(column_roles, dataset_type_info, df)
        return {"dataset_type": dataset_type_info["type"], "analyses": tx_plan["analyses"]}

    analyses = [_supervised_entry(target_candidates), _segmentation_entry(df, profile)]
    return {"dataset_type": dataset_type_info["type"], "analyses": analyses}
