import hashlib

from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.models.dataset import Dataset
from app.models.ml_model import MLModel
from app.models.project import Project
from app.models.report import Report
from app.services import cleaning_service, eda_service
from app.services.report_context_service import CHURN_TARGET_HINTS, CUSTOMER_ID_HINTS
from app.utils.feature_names import humanize_column_name, humanize_feature_name


def compute_insight_cache_key(dataset: Dataset | None, model: MLModel | None) -> str:
    """A short, deterministic fingerprint of 'what was the insight generated about' —
    the dataset's cleaning version (bumps on every re-clean) and the trained model's id
    (a NEW row per training run in this app, so it already uniquely identifies one
    specific trained model). Changing either invalidates any cached insight for it."""
    parts = [
        str(dataset.id) if dataset else "no-dataset",
        str(dataset.cleaning_version) if dataset else "0",
        str(model.id) if model else "no-model",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def build_dataset_context(dataset: Dataset) -> dict:
    profile = dataset.profile_json or {}
    dataset_context = {
        "file_name": dataset.file_name,
        "rows": dataset.row_count,
        "columns": dataset.column_count,
        # These reflect the CURRENT (possibly already-cleaned) dataset — see
        # data_quality_note below for the original, as-uploaded figures, so the AI never
        # claims the raw file had zero missing values just because the cleaned copy does.
        "missing_values": dataset.missing_values,
        "duplicate_rows": dataset.duplicate_rows,
        "numeric_columns": profile.get("numeric_columns", []),
        "categorical_columns": profile.get("categorical_columns", []),
        "is_cleaned": dataset.is_cleaned,
    }

    log = dataset.cleaning_log_json or {}
    if dataset.is_cleaned and log:
        original_missing = log.get("missing_cells_before")
        original_duplicates = log.get("duplicates_removed")
        dataset_context["data_quality_note"] = (
            f"{original_missing if original_missing is not None else 'an unknown number of'} missing cell(s) "
            f"and {original_duplicates if original_duplicates is not None else 'an unknown number of'} duplicate "
            f"row(s) were found in the ORIGINAL uploaded file and were filled/removed during cleaning. The "
            f"current cleaned dataset now has {dataset.missing_values} missing value(s) and "
            f"{dataset.duplicate_rows} duplicate row(s) remaining."
        )

    return {"dataset": dataset_context}


def build_eda_context(db: Session, dataset: Dataset) -> dict:
    run = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.dataset_id == dataset.id, AnalysisRun.run_type == "eda")
        .order_by(AnalysisRun.created_at.desc())
        .first()
    )
    if not run:
        return {}
    results = run.results_json or {}
    return {
        "eda_summary": results.get("summary", {}),
        "outliers": {k: v for k, v in (results.get("outliers") or {}).items() if v.get("count", 0) > 0},
    }


def build_model_context(model: MLModel) -> dict:
    metrics = model.metrics_json or {}
    context = {
        "target": model.target_column,
        "model": {
            "type": model.model_type,
            "problem_type": model.problem_type,
            "metrics": metrics,
        },
        "important_features": (model.feature_importance_json or [])[:8],
    }
    if metrics.get("is_weak"):
        # Explicit, hard-to-miss verdict alongside the raw metrics — the system prompt
        # instructs the AI to state this plainly and skip feature-importance-based
        # recommendations when it's present, rather than relying on the LLM correctly
        # interpreting a borderline ROC-AUC value buried in the metrics dict on its own.
        context["model_quality_verdict"] = {
            "is_weak": True,
            "reason": metrics.get("weak_reason"),
        }
    return context


# ---------------------------------------------------------------------------
# The functions below are chat-specific additions layered on top of the three
# functions above. They are called ONLY from build_project_context (AI Chat's context
# builder) — never from AI Insights or Auto Analyze, which call build_dataset_context /
# build_eda_context / build_model_context directly and must keep seeing exactly the
# shape they already do. Keeping these separate means enriching chat's context carries
# zero risk of changing Insights/Auto-Analyze's behavior.
# ---------------------------------------------------------------------------


def _chat_column_details(dataset: Dataset) -> list[dict]:
    """A compact per-column dictionary (name, kind, missing %) — no raw values — so the
    AI can reference real column names and know what's numeric/categorical/date/ID
    without needing the full profile dump."""
    columns = (dataset.profile_json or {}).get("columns", [])
    details = []
    for col in columns:
        kind = (
            "identifier"
            if col.get("is_id_like")
            else "datetime"
            if col.get("is_datetime")
            else "numeric"
            if col.get("is_numeric")
            else "categorical"
        )
        details.append(
            {
                "name": col["name"],
                "label": humanize_column_name(col["name"]),
                "kind": kind,
                "missing_pct": col.get("missing_pct", 0),
            }
        )
    return details


def _chat_data_quality(dataset: Dataset) -> dict:
    """Original (as-uploaded) quality issues, summarized by type, plus what cleaning
    actually did — reuses the same analyze_quality() the Data Quality tab and the report
    use, so the numbers can never drift from what's shown elsewhere in the app."""
    try:
        original_issues = cleaning_service.analyze_quality(dataset)
    except Exception:
        return {}

    by_type: dict[str, list[dict]] = {}
    for issue in original_issues:
        by_type.setdefault(issue["type"], []).append(
            {"column": issue["column"], "message": issue["message"], "affected_count": issue["affected_count"]}
        )
    # Keep at most 5 examples per issue type — enough for the AI to speak concretely
    # without paying the token cost of every single column's issue.
    summarized = {t: {"count": len(items), "examples": items[:5]} for t, items in by_type.items()}

    quality: dict = {"original_issues_by_type": summarized}

    log = dataset.cleaning_log_json or {}
    if dataset.is_cleaned and log:
        quality["cleaning_performed"] = {
            "duplicates_removed": log.get("duplicates_removed", 0),
            "columns_filled": list((log.get("per_column_strategy") or {}).keys()),
            "impossible_values_corrected": log.get("impossible_negative_values_corrected", {}),
        }
    else:
        quality["cleaning_performed"] = None

    return quality


def _top_correlated_pairs(correlation: dict, limit: int = 5) -> list[dict]:
    columns = correlation.get("columns", [])
    matrix = correlation.get("matrix", [])
    pairs = []
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            if i >= len(matrix) or j >= len(matrix[i]):
                continue
            value = matrix[i][j]
            if value is None:
                continue
            pairs.append({"columns": [columns[i], columns[j]], "correlation": round(float(value), 3)})
    pairs.sort(key=lambda p: abs(p["correlation"]), reverse=True)
    return pairs[:limit]


def _chat_eda_findings(db: Session, dataset: Dataset) -> dict:
    """Distributions/categories/trends from the side-effect-free filtered-EDA engine
    (no DB writes, safe to call on every chat turn), plus correlations from the latest
    EDA run the user has already explicitly generated (read-only — chat never triggers a
    new EDA run itself, so a question never silently creates analysis history)."""
    findings: dict = {}
    try:
        result = eda_service.run_filtered_eda(db, dataset, categorical_filters=None, date_filters=None)
    except Exception:
        result = None

    if result:
        findings["kpis"] = [{"label": k["label"], "value": k["value"]} for k in result.get("kpis", [])]
        findings["top_categories"] = [
            {
                "column": chart["label"],
                "top_values": [{"value": i["value"], "pct": i["pct"]} for i in chart["items"][:5]],
            }
            for chart in (result.get("donut_charts", []) + result.get("bar_charts", []))
        ]
        if result.get("time_trend"):
            tt = result["time_trend"]
            findings["trend_over_time"] = {
                "date_column": tt["date_column"],
                "periods": len(tt["labels"]),
                "series": [s["name"] for s in tt["series"]],
            }
        if result.get("rating_breakdown"):
            rb = result["rating_breakdown"]
            findings["distribution"] = {"column": rb["label"], "average": rb["average"], "range": [rb["min"], rb["max"]]}

    run = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.dataset_id == dataset.id, AnalysisRun.run_type == "eda")
        .order_by(AnalysisRun.created_at.desc())
        .first()
    )
    if run and run.results_json:
        correlation = run.results_json.get("correlation") or {}
        top_pairs = _top_correlated_pairs(correlation)
        if top_pairs:
            findings["strongest_correlations"] = top_pairs

    return findings


def _chat_model_extras(model: MLModel, known_columns: list[str]) -> dict:
    """Humanized feature usage/exclusion detail, reusing the exact leakage/post-outcome/
    high-correlation warnings already stored on the model from training — never
    recomputed here, so it can't drift from what Modeling/Reports show."""
    metrics = model.metrics_json or {}
    preprocessing = model.preprocessing_json or {}
    return {
        "features_used": [humanize_feature_name(f, known_columns) for f in (model.feature_columns_json or [])],
        "features_excluded": {
            "id_like_columns": [humanize_column_name(c) for c in preprocessing.get("excluded_id_like", [])],
            "leakage": [
                {"column": humanize_feature_name(w["column"], known_columns), "reason": w["reason"]}
                for w in metrics.get("leakage_warnings", [])
            ],
            "post_outcome_excluded_by_default": [
                {"column": humanize_feature_name(w["column"], known_columns), "reason": w["reason"]}
                for w in metrics.get("post_outcome_excluded", [])
            ],
            "high_correlation_retained_but_flagged": [
                {"column": humanize_feature_name(w["column"], known_columns), "correlation": w["correlation"]}
                for w in metrics.get("high_correlation_warnings", [])
            ],
        },
        "cross_validation": {
            "mean": metrics.get("cv_mean"),
            "scores": metrics.get("cv_scores"),
        },
        "baseline_comparison": metrics.get("baseline"),
    }


def _chat_customer_identity(dataset: Dataset, model: MLModel | None) -> dict:
    """Whether this dataset has a real customer identifier — the AI must not claim
    individual customer-level predictions/retention findings without this, and must not
    confuse a product-level outcome (e.g. 'returned') with customer churn."""
    columns = (dataset.profile_json or {}).get("columns", [])
    customer_id_column = next(
        (c["name"] for c in columns if c.get("is_id_like") and any(h in c["name"].lower() for h in CUSTOMER_ID_HINTS)),
        None,
    )
    note = {
        "has_customer_identifier": customer_id_column is not None,
        "customer_identifier_column": customer_id_column,
    }
    if model:
        target_tokens = model.target_column.lower()
        note["target_is_churn_like"] = any(h in target_tokens for h in CHURN_TARGET_HINTS)
        if note["target_is_churn_like"] and not customer_id_column:
            note["guidance"] = (
                "This dataset has no customer identifier/history — do not claim which individual "
                "customers will churn or return; explain that customer-level prediction isn't possible here."
            )
    return note


def _chat_analysis_stage(dataset: Dataset | None, model: MLModel | None, has_report: bool) -> str:
    if not dataset:
        return "no_dataset_uploaded"
    if has_report:
        return "report_generated"
    if model:
        return "model_trained"
    if dataset.is_cleaned:
        return "data_cleaned"
    return "dataset_uploaded"


def _chat_report_summary(db: Session, project: Project) -> dict | None:
    """Only the latest report's title and its already-generated AI narrative — both
    genuinely stored, never re-derived here (report generation itself is untouched)."""
    report = (
        db.query(Report)
        .filter(Report.project_id == project.id)
        .order_by(Report.created_at.desc())
        .first()
    )
    if not report:
        return None
    content = report.content_json or {}
    return {
        "title": report.title,
        "generated_at": str(report.created_at),
        "ai_insight": content.get("ai_insight"),
    }


def build_project_context(db: Session, project: Project) -> dict:
    context: dict = {
        "project": {
            "name": project.name,
            "description": project.description,
            "status": project.status,
        }
    }
    dataset: Dataset | None = None
    datasets = list(project.datasets)
    if datasets:
        dataset = sorted(datasets, key=lambda d: d.created_at)[-1]
        context.update(build_dataset_context(dataset))
        context.update(build_eda_context(db, dataset))
        context["dataset"]["datetime_columns"] = (dataset.profile_json or {}).get("datetime_columns", [])
        context["dataset"]["columns_detail"] = _chat_column_details(dataset)
        context["data_quality"] = _chat_data_quality(dataset)
        eda_findings = _chat_eda_findings(db, dataset)
        if eda_findings:
            context["eda_findings"] = eda_findings

    models = sorted(project.models, key=lambda m: m.created_at)
    best_model = next((m for m in models if m.is_best), models[-1] if models else None)
    if best_model:
        context.update(build_model_context(best_model))
        context["all_models"] = [
            {"type": m.model_type, "metrics": m.metrics_json} for m in models
        ]
        known_columns = [c["name"] for c in (dataset.profile_json or {}).get("columns", [])] if dataset else []
        context["model"].update(_chat_model_extras(best_model, known_columns))

    if dataset:
        context["dataset_identity"] = _chat_customer_identity(dataset, best_model)

    report_summary = _chat_report_summary(db, project)
    if report_summary:
        context["latest_report"] = report_summary

    context["analysis_stage"] = _chat_analysis_stage(dataset, best_model, report_summary is not None)

    return context
