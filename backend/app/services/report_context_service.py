"""Assembles the single, structured, verified data context a report is built from — the
PDF template, the HTML preview, and the AI Business Insights prompt all render from
exactly this dict, so they can never contradict each other. Every value here comes from
already-computed backend results (pandas/scikit-learn output, stored model metrics, the
dataset's own profile) — nothing is invented here."""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.models.dataset import Dataset
from app.models.ml_model import MLModel
from app.models.project import Project
from app.services import cleaning_service, eda_service, report_charts
from app.services.data_understanding_service import classify_columns
from app.services.ml_service import MODEL_LABELS
from app.services.dataset_service import (
    find_ungrounded_concepts,
    load_dataframe,
    load_original_dataframe,
)
from app.utils.feature_names import humanize_column_name, humanize_feature_name, humanize_feature_list
from app.utils.formatting import detect_currency_from_column_name, format_money

CHURN_TARGET_HINTS = ("churn", "retention", "retain", "attrition", "cancel", "unsubscribe")
CUSTOMER_ID_HINTS = ("customer", "client", "subscriber", "member", "user")


def _detect_report_currency(dataset: Dataset | None) -> str | None:
    """Scans the dataset's own column names for a currency code (e.g. 'total_pkr') — a
    dataset's transactions are effectively always one currency, so this is computed once
    per report rather than per value. Returns None (no currency prefix anywhere) when the
    data gives no such evidence, rather than defaulting to a guessed currency."""
    if not dataset:
        return None
    for col in (dataset.profile_json or {}).get("columns", []):
        currency = detect_currency_from_column_name(col.get("name"))
        if currency:
            return currency
    return None

METRIC_LABELS = {
    "mae": "Mean Absolute Error (MAE)",
    "mse": "Mean Squared Error (MSE)",
    "rmse": "Root Mean Squared Error (RMSE)",
    "r2": "R² (variance explained)",
    "mape": "Mean Absolute Percentage Error",
    "accuracy": "Accuracy",
    "precision": "Precision",
    "recall": "Recall",
    "f1": "F1 Score",
    "roc_auc": "ROC-AUC",
}


def _fmt_num(value, digits=2):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _known_columns(dataset: Dataset) -> list[str]:
    return [c["name"] for c in (dataset.profile_json or {}).get("columns", [])]


def _build_dataset_section(dataset: Dataset, project: Project) -> dict:
    profile = dataset.profile_json or {}
    columns = profile.get("columns", [])
    df = load_dataframe(dataset)

    time_period = None
    for col in columns:
        if col.get("is_datetime"):
            series = df[col["name"]]
            parsed = pd.to_datetime(series, errors="coerce")
            # Excludes invalid/implausible dates (unparseable, future, or a stray outlier
            # far outside the bulk of the column's own range — e.g. a planted 2099 date
            # among otherwise-2025/2026 rows) so the reported date range reflects the
            # dataset's real span, not a single bad value.
            valid = parsed[~cleaning_service.flag_invalid_dates(series) & parsed.notna()]
            if len(valid) > 0:
                time_period = {
                    "column": humanize_column_name(col["name"]),
                    "min": str(valid.min().date()),
                    "max": str(valid.max().date()),
                }
            break

    column_dict = []
    for col in columns:
        kind = (
            "Identifier"
            if col.get("is_id_like")
            else "Date/Time"
            if col.get("is_datetime")
            else "Numeric"
            if col.get("is_numeric")
            else "Category"
        )
        detail = ""
        stats = col.get("stats")
        if stats and stats.get("mean") is not None:
            detail = f"typical range {_fmt_num(stats.get('min'))}–{_fmt_num(stats.get('max'))}"
        elif col.get("top_values"):
            top = col["top_values"][0]
            detail = f"most common: {top['value']}"
        column_dict.append(
            {
                "name": col["name"],
                "label": humanize_column_name(col["name"]),
                "kind": kind,
                "missing_pct": col.get("missing_pct", 0),
                "detail": detail,
            }
        )

    description = project.description or None
    ungrounded = find_ungrounded_concepts(description, [c["name"] for c in columns]) if description else []

    return {
        "file_name": dataset.file_name,
        "row_count": profile.get("row_count", dataset.row_count),
        "column_count": profile.get("column_count", dataset.column_count),
        "time_period": time_period,
        "description": description,
        "ungrounded_concepts": [humanize_column_name(w) for w in ungrounded],
        "columns": column_dict,
    }


def _build_data_quality_section(dataset: Dataset) -> dict:
    original_issues = cleaning_service.analyze_quality(dataset)
    original_df = load_original_dataframe(dataset)
    issues_by_type: dict[str, list[dict]] = {}
    for issue in original_issues:
        issues_by_type.setdefault(issue["type"], []).append(issue)

    original = {
        "total_rows": int(len(original_df)),
        "missing_cells": int(original_df.isna().sum().sum()),
        "duplicate_rows": int(original_df.duplicated().sum()),
        "issues_by_type": issues_by_type,
        "issue_count": len(original_issues),
    }

    cleaning_actions: list[dict] = []
    log = dataset.cleaning_log_json or {}
    original_missing_by_col = {
        i["column"]: i["affected_count"] for i in original_issues if i["type"] == "missing_values"
    }

    if log.get("duplicates_removed"):
        cleaning_actions.append(
            {
                "problem": "Duplicate rows",
                "column": "— (whole row)",
                "before": f"{log['duplicates_removed']} duplicate row(s)",
                "action": "Removed duplicate rows",
                "after": "0 duplicate rows",
                "reason": "Duplicate records would otherwise double-count the same transaction/customer.",
            }
        )

    impossible_cols = set((log.get("impossible_negative_values_corrected") or {}).keys())
    for col, strategy in (log.get("per_column_strategy") or {}).items():
        before_count = original_missing_by_col.get(col)
        if before_count is None and col in impossible_cols:
            # This column had zero genuine missing values in the ORIGINAL file — its
            # "missingness" was entirely created by the impossible-negative-value
            # correction below, which already documents this column's before/after in
            # full. A separate "Missing X" row here would double-report the same rows.
            continue
        strategy_label = {
            "median": "Filled using the column median",
            "mean": "Filled using the column mean",
            "mode": "Filled using the most frequent value (mode)",
            "drop": "Rows with a missing value dropped",
            "constant": "Filled using a fixed value",
        }.get(strategy, f"Filled using '{strategy}'")
        reason = {
            "median": "Reduces the influence of extreme values compared to the mean.",
            "mean": "A simple, unbiased estimate for a roughly symmetric distribution.",
            "mode": "The most representative value for a categorical field.",
            "drop": "No safe way to estimate a missing value for this field.",
            "constant": "A fixed placeholder value was specified.",
        }.get(strategy, "Selected automatically based on the column's data type.")
        cleaning_actions.append(
            {
                "problem": f"Missing {humanize_column_name(col)}",
                "column": humanize_column_name(col),
                "before": f"{before_count} missing" if before_count is not None else "missing values present",
                "action": strategy_label,
                "after": "0 missing",
                "reason": reason,
            }
        )

    for col, count in (log.get("impossible_negative_values_corrected") or {}).items():
        cleaning_actions.append(
            {
                "problem": f"Impossible negative {humanize_column_name(col)}",
                "column": humanize_column_name(col),
                "before": f"{count} impossible value(s)",
                "action": "Converted to missing, then filled",
                "after": "0 impossible values",
                "reason": "A rare negative value in an otherwise non-negative column is almost always a data-entry error.",
            }
        )

    for col, info in (log.get("unit_stripped_columns") or {}).items():
        # `info` is either the new {"match_rate", "example"} shape or, for a report built
        # from an older cleaning_log_json predating this change, a bare float — handled so
        # an old report still renders instead of crashing.
        rate = info.get("match_rate") if isinstance(info, dict) else info
        example = info.get("example") if isinstance(info, dict) else None
        before_text = f"stored as text with a unit (e.g. '{example}')" if example else "stored as text with a unit suffix"
        cleaning_actions.append(
            {
                "problem": f"Text-formatted numbers in {humanize_column_name(col)}",
                "column": humanize_column_name(col),
                "before": before_text,
                "action": "Converted to a clean numeric value",
                "after": "numeric",
                "reason": f"{round(rate * 100)}% of values matched a number-with-unit pattern.",
            }
        )

    for col, mapping in (log.get("text_case_normalized_columns") or {}).items():
        variant, canonical = next(iter(mapping.items()))
        example_text = f"(e.g. '{variant}' vs '{canonical}')" if variant != canonical else ""
        cleaning_actions.append(
            {
                "problem": f"Inconsistent capitalization in {humanize_column_name(col)}",
                "column": humanize_column_name(col),
                "before": f"{len(mapping)} variant label(s) {example_text}".strip(),
                "action": "Merged into a single canonical label",
                "after": "consistent labels",
                "reason": "Values differing only by letter case would otherwise be treated as different categories.",
            }
        )

    return {
        "original": original,
        "is_cleaned": dataset.is_cleaned,
        "cleaning_actions": cleaning_actions,
        "current": {
            "missing_cells": dataset.missing_values,
            "duplicate_rows": dataset.duplicate_rows,
            "row_count": dataset.row_count,
        },
    }


def _build_eda_section(db: Session, dataset: Dataset) -> dict:
    result = eda_service.run_filtered_eda(db, dataset, categorical_filters=None, date_filters=None)

    donut_charts = []
    for chart in result["donut_charts"][:2]:
        labels = [item["value"] for item in chart["items"]]
        values = [item["count"] for item in chart["items"]]
        donut_charts.append({"label": chart["label"], "image": report_charts.donut_chart(labels, values, chart["label"])})

    bar_charts = []
    for chart in result["bar_charts"][:2]:
        items = sorted(chart["items"], key=lambda i: i["count"], reverse=True)[:8]
        labels = [item["value"] for item in items]
        values = [item["count"] for item in items]
        bar_charts.append({"label": chart["label"], "image": report_charts.bar_chart(labels, values, chart["label"])})

    time_trend_image = None
    time_trend_label = None
    peak_month = None
    if result["time_trend"] and len(result["time_trend"]["labels"]) >= 2:
        tt = result["time_trend"]
        time_trend_label = f"Trend over time ({humanize_column_name(tt['date_column'])})"
        time_trend_image = report_charts.line_chart(tt["labels"], tt["series"], time_trend_label)
        peak_month = tt.get("peak_month")

    return {
        "kpis": result["kpis"],
        "donut_charts": donut_charts,
        "bar_charts": bar_charts,
        "time_trend_image": time_trend_image,
        "time_trend_label": time_trend_label,
        "peak_month": peak_month,
        "rating_breakdown": result["rating_breakdown"],
    }


def _technical_box(model: MLModel) -> dict:
    preprocessing = model.preprocessing_json or {}
    return {
        "algorithm": MODEL_LABELS.get(model.model_type, model.model_type),
        "numeric_features": len(preprocessing.get("numeric_features", [])),
        "categorical_features": len(preprocessing.get("categorical_features", [])),
        "test_size": preprocessing.get("test_size"),
        "cv_folds": preprocessing.get("cv_folds"),
        "random_seed": model.random_seed,
        "hyperparameters": model.hyperparameters_json or {},
    }


def _metrics_explained(problem_type: str, metrics: dict) -> list[str]:
    sentences = []
    if problem_type == "regression":
        if metrics.get("mae") is not None:
            sentences.append(f"On average, predictions are off by about {_fmt_num(metrics['mae'])} units (Mean Absolute Error).")
        if metrics.get("r2") is not None:
            pct = round(float(metrics["r2"]) * 100, 1)
            sentences.append(f"The model explains about {pct}% of the variation in the target (R² = {_fmt_num(metrics['r2'], 3)}).")
    else:
        if metrics.get("accuracy") is not None:
            sentences.append(f"The model correctly classifies about {round(float(metrics['accuracy']) * 100, 1)}% of cases (Accuracy).")
        if metrics.get("f1") is not None:
            sentences.append(f"F1 score (balancing precision and recall) is {_fmt_num(metrics['f1'], 3)}.")
        if metrics.get("roc_auc") is not None:
            sentences.append(f"ROC-AUC is {_fmt_num(metrics['roc_auc'], 3)} (1.0 = perfect separation, 0.5 = no better than chance).")
    return sentences


def _build_model_section(model: MLModel, dataset: Dataset) -> dict:
    metrics = dict(model.metrics_json or {})
    known_columns = _known_columns(dataset)
    preprocessing = model.preprocessing_json or {}

    leakage = [
        {**w, "label": humanize_feature_name(w["column"], known_columns)}
        for w in metrics.get("leakage_warnings", [])
    ]
    post_outcome_excluded = [
        {**w, "label": humanize_feature_name(w["column"], known_columns)}
        for w in metrics.get("post_outcome_excluded", [])
    ]
    post_outcome_included = [
        {**w, "label": humanize_feature_name(w["column"], known_columns)}
        for w in metrics.get("post_outcome_included", [])
    ]
    high_correlation = [
        {**w, "label": humanize_feature_name(w["column"], known_columns)}
        for w in metrics.get("high_correlation_warnings", [])
    ]

    charts = {}
    if model.problem_type == "regression" and metrics.get("actual_vs_predicted"):
        pairs = metrics["actual_vs_predicted"]
        charts["actual_vs_predicted"] = report_charts.scatter_actual_vs_predicted(
            [p["actual"] for p in pairs], [p["predicted"] for p in pairs]
        )
    if model.problem_type == "classification" and metrics.get("confusion_matrix"):
        cm = metrics["confusion_matrix"]
        charts["confusion_matrix"] = report_charts.confusion_matrix_chart(cm["labels"], cm["matrix"])

    comparison_table = []
    metric_keys = (
        ["mae", "rmse", "r2"] if model.problem_type == "regression" else ["accuracy", "precision", "recall", "f1", "roc_auc"]
    )
    for entry in (model.comparison_json or {}).get("models", []):
        row = {"label": entry["label"]}
        for key in metric_keys:
            row[key] = entry["metrics"].get(key)
        comparison_table.append(row)

    if len(comparison_table) > 1:
        charts["model_comparison"] = report_charts.grouped_bar_chart(
            [row["label"] for row in comparison_table],
            [{"name": METRIC_LABELS.get(k, k), "values": [row.get(k) or 0 for row in comparison_table]} for k in metric_keys[:2]],
            "Model comparison",
        )

    return {
        "target_column": model.target_column,
        "target_label": humanize_column_name(model.target_column),
        "problem_type": model.problem_type,
        "model_type": model.model_type,
        "model_type_label": MODEL_LABELS.get(model.model_type, model.model_type),
        "features_used": [{"name": f, "label": humanize_feature_name(f, known_columns)} for f in (model.feature_columns_json or [])],
        "excluded_id_like": [humanize_column_name(c) for c in preprocessing.get("excluded_id_like", [])],
        "excluded_datetime_raw": [humanize_column_name(c) for c in preprocessing.get("excluded_datetime_raw", [])],
        "derived_date_features": [humanize_feature_name(c, known_columns) for c in preprocessing.get("derived_date_features", [])],
        "leakage_warnings": leakage,
        "post_outcome_excluded": post_outcome_excluded,
        "post_outcome_included": post_outcome_included,
        "high_correlation_warnings": high_correlation,
        "technical_box": _technical_box(model),
        "metrics": metrics,
        "metric_labels": METRIC_LABELS,
        "metrics_explained": _metrics_explained(model.problem_type, metrics),
        "comparison_table": comparison_table,
        "metric_keys": metric_keys,
        "charts": charts,
        "is_weak": bool(metrics.get("is_weak")),
        "weak_reason": metrics.get("weak_reason"),
        "baseline": metrics.get("baseline"),
        "cv_mean": metrics.get("cv_mean"),
        "cv_scores": metrics.get("cv_scores"),
    }


def _build_feature_importance(model: MLModel, dataset: Dataset) -> list[dict]:
    known_columns = _known_columns(dataset)
    items = humanize_feature_list(model.feature_importance_json or [], known_columns)
    return items[:10]


def _build_future_outlook(db: Session, model: MLModel, dataset: Dataset, feature_importance: list[dict]) -> dict:
    known_columns = _known_columns(dataset)
    target_tokens = model.target_column.lower()
    is_churn_like = any(h in target_tokens for h in CHURN_TARGET_HINTS)
    has_customer_id = any(
        c.get("is_id_like") and any(h in c["name"].lower() for h in CUSTOMER_ID_HINTS)
        for c in (dataset.profile_json or {}).get("columns", [])
    )

    if model.problem_type == "regression":
        top_features = feature_importance[:3]
        target_label = humanize_column_name(model.target_column)
        rank_phrase = ("the strongest single predictor of", "the second most influential predictor of", "also a meaningful predictor of")
        narrative = [
            f"'{f['label']}' is {rank_phrase[min(i, 2)]} {target_label} in this model "
            f"(relative importance {round(f['importance'] * 100, 1)}%)."
            for i, f in enumerate(top_features)
        ]
        pairs = (model.metrics_json or {}).get("actual_vs_predicted", [])[:5]
        return {
            "kind": "regression",
            "narrative": narrative,
            "examples": pairs,
            "groups": None,
        }

    metrics = model.metrics_json or {}
    class_distribution = metrics.get("class_distribution") or {}
    top_class = max(class_distribution, key=class_distribution.get) if class_distribution else None

    if is_churn_like and not has_customer_id:
        return {
            "kind": "churn_no_customer_id",
            "narrative": [
                "This dataset cannot determine which individual customers will return or leave, because it "
                "does not contain a customer identifier or historical customer-level records — only a flat "
                "table of rows without a way to track a specific customer over time."
            ],
            "groups": None,
        }

    df = load_dataframe(dataset)
    groups = []
    if feature_importance and model.target_column in df.columns:
        # Walk features in IMPORTANCE order and resolve each back to its real base
        # column (a one-hot encoded name like 'region_North' -> 'region'; a raw numeric
        # feature like 'tenure_months' resolves to itself, not a naive first-underscore
        # split, which would wrongly truncate it to 'tenure') — take the first one that's
        # a genuine numeric column, so the grouping reflects the MOST important numeric
        # driver, not whichever numeric column happens to appear first in the profile.
        candidate = None
        for f in feature_importance:
            matches = [c for c in known_columns if f["name"] == c or f["name"].startswith(f"{c}_")]
            if not matches:
                continue
            base = max(matches, key=len)
            col_profile = next((c for c in (dataset.profile_json or {}).get("columns", []) if c["name"] == base), None)
            if col_profile and col_profile.get("is_numeric") and base in df.columns:
                candidate = base
                break
        if candidate and pd.api.types.is_numeric_dtype(df[candidate]):
            try:
                bins = pd.qcut(df[candidate].rank(method="first"), q=3, labels=["Lower", "Middle", "Higher"])
                target_numeric = pd.factorize(df[model.target_column].astype(str))[0]
                grouped = pd.DataFrame({"bin": bins, "target": target_numeric}).groupby("bin", observed=True)["target"].mean()
                for label, rate in grouped.items():
                    groups.append(
                        {
                            "label": f"{humanize_column_name(candidate)}: {label}",
                            "rate": round(float(rate), 4),
                            "size": int((bins == label).sum()),
                        }
                    )
            except Exception:
                groups = []

    label = "customer" if (is_churn_like and has_customer_id) else "record"
    kind = "churn_customer_level" if (is_churn_like and has_customer_id) else "classification_generic"
    narrative = []
    if top_class is not None:
        narrative.append(
            f"The most common observed outcome for {humanize_column_name(model.target_column)} is "
            f"'{top_class}' ({class_distribution[top_class]} of {sum(class_distribution.values())} test cases)."
        )
    if groups:
        highest = max(groups, key=lambda g: g["rate"])
        lowest = min(groups, key=lambda g: g["rate"])
        narrative.append(
            f"Among {label}s, the '{highest['label']}' group shows the highest observed rate "
            f"({round(highest['rate'] * 100, 1)}%, n={highest['size']}), compared to "
            f"'{lowest['label']}' ({round(lowest['rate'] * 100, 1)}%, n={lowest['size']})."
        )
    return {"kind": kind, "narrative": narrative, "groups": groups, "label": label}


def _build_model_recommendations(model: MLModel, future_outlook: dict, feature_importance: list[dict]) -> list[str]:
    """Real, rule-based BUSINESS ACTIONS for a trained supervised model — deliberately
    NOT a restatement of feature importance ("X is the strongest predictor of Y" is an
    analytical finding, not something a reader can act on). Built from the same verified
    data future_outlook already computed (grouped observed rates, actual-vs-predicted
    examples), phrased as an action rather than a ranking."""
    recs: list[str] = []
    metrics = model.metrics_json or {}

    if metrics.get("is_weak"):
        recs.append(
            f"This model's predictions are not reliable enough to act on directly "
            f"({metrics.get('weak_reason') or 'it does not clearly beat a naive baseline'}) — before using it "
            "to guide decisions, consider collecting more data, adding features with a stronger real "
            "relationship to the target, or reconsidering whether this target is predictable from the "
            "available columns at all."
        )
        return recs

    target_label = humanize_column_name(model.target_column)

    if future_outlook.get("kind") == "regression":
        if feature_importance:
            top = feature_importance[0]
            recs.append(
                f"'{top['label']}' is the leading driver of {target_label} in this model — treat it as the "
                "primary lever: track it closely, and test whether deliberately changing it (through pricing, "
                f"process, or policy changes) moves {target_label} in the expected direction."
            )
    elif future_outlook.get("groups"):
        groups = future_outlook["groups"]
        highest = max(groups, key=lambda g: g["rate"])
        lowest = min(groups, key=lambda g: g["rate"])
        gap = round((highest["rate"] - lowest["rate"]) * 100, 1)
        if gap >= 1:
            recs.append(
                f"Prioritize outreach/intervention on the '{highest['label']}' segment ({highest['size']} "
                f"cases, {round(highest['rate'] * 100, 1)}% observed {target_label} rate) — it shows a "
                f"{gap} point higher rate than '{lowest['label']}' ({round(lowest['rate'] * 100, 1)}%), the "
                "clearest actionable lever this model identifies."
            )

    baseline = metrics.get("baseline") or {}
    if baseline.get("clearly_beats_baseline"):
        recs.append(
            f"This model clearly outperforms a naive baseline ({baseline.get('baseline_metric')}: "
            f"{baseline.get('model_score')} vs. {baseline.get('baseline_score')}) — reasonable to use for "
            "prioritization, though any single prediction should still be sanity-checked against domain "
            "knowledge before a high-stakes decision."
        )

    return recs


def _build_limitations(dataset: Dataset, model: MLModel | None) -> list[str]:
    limitations = []
    row_count = dataset.row_count or 0
    if row_count < 200:
        limitations.append(
            f"This dataset has only {row_count} rows — results and model performance should be treated as "
            "preliminary; a larger sample would give more reliable estimates."
        )
    if model:
        metrics = model.metrics_json or {}
        if metrics.get("is_weak"):
            limitations.append(f"Model reliability warning: {metrics.get('weak_reason')}")
        if metrics.get("leakage_warnings"):
            cols = ", ".join(w["column"] for w in metrics["leakage_warnings"])
            limitations.append(
                f"The following column(s) were excluded as likely data leakage and could not be used: {cols}. "
                "If any of these should genuinely be available before prediction time, review them manually."
            )
        if metrics.get("high_correlation_warnings"):
            cols = ", ".join(w["column"] for w in metrics["high_correlation_warnings"])
            limitations.append(
                f"Column(s) with high correlation to the target were retained but should be manually verified "
                f"as legitimate, known-in-advance predictors: {cols}."
            )
        cv_std = (metrics.get("baseline") or {}).get("cv_std")
        if cv_std:
            limitations.append(
                f"Cross-validation scores varied by ±{_fmt_num(cv_std, 3)} across folds — treat the headline "
                "metric as an estimate with this much natural uncertainty, not an exact figure."
            )
    limitations.append(
        "Model predictions are estimates based on historical patterns and may not hold if underlying "
        "business conditions change. They should inform, not replace, domain expertise."
    )
    return limitations


def _build_clusters_section(clusters: dict, dataset: Dataset) -> dict:
    """Humanizes the raw output of ml_service.run_clustering_analysis() for the report —
    used only for the 'General Analysis' (no-target) path, mutually exclusive with the
    'model' section (a job either trains a supervised model or clusters, never both).

    Enriches each cluster with a rule-based descriptive NAME (from its most distinctive
    behavioral features vs. the overall dataset average — never an arbitrary cluster ID
    alone), a comparison-to-average table (what's actually notable, not just raw
    averages), the demographic/categorical descriptive_summary ml_service already
    computed but the report never surfaced, and a rule-based suggested offer — so a
    reader gets an actionable segment profile."""
    profiles_raw = clusters.get("clusters", [])
    behavioral_cols = clusters.get("features_used", [])
    total_rows = sum(c["size"] for c in profiles_raw) or 1

    overall_means = {
        col: sum(c.get("feature_means", {}).get(col, 0) * c["size"] for c in profiles_raw) / total_rows
        for col in behavioral_cols
    }

    # Which behavioral column (if any) represents monetary value — used to rank clusters
    # by value for the suggested-offer rule below. Detected from real column roles
    # (classify_columns), never a hardcoded per-dataset column name.
    money_col = None
    try:
        df = load_dataframe(dataset)
        column_roles = classify_columns(df, dataset.profile_json or {}, use_gemini=False)
        money_col = next((c for c in behavioral_cols if column_roles.get(c, {}).get("role") == "money"), None)
    except Exception:
        pass

    value_rank: dict[int, int] = {}
    if money_col:
        ranked = sorted(profiles_raw, key=lambda c: c.get("feature_means", {}).get(money_col, 0), reverse=True)
        value_rank = {c["cluster"]: i for i, c in enumerate(ranked)}

    profiles = []
    for c in profiles_raw:
        deviations = []
        for name, value in c.get("feature_means", {}).items():
            overall = overall_means.get(name, 0)
            pct_diff = ((value - overall) / abs(overall) * 100) if overall else 0.0
            deviations.append(
                {
                    "label": humanize_column_name(name),
                    "value": _fmt_num(value),
                    "overall_value": _fmt_num(overall),
                    "pct_diff": round(pct_diff, 1),
                }
            )
        deviations.sort(key=lambda d: abs(d["pct_diff"]), reverse=True)

        name_parts = []
        for d in deviations[:2]:
            if abs(d["pct_diff"]) >= 10:  # only call out a genuinely notable difference
                direction = "High" if d["pct_diff"] > 0 else "Low"
                name_parts.append(f"{direction} {d['label']}")
        segment_name = ", ".join(name_parts) if name_parts else f"Cluster {c['cluster']} (near-average profile)"

        if money_col and c["cluster"] in value_rank:
            rank = value_rank[c["cluster"]]
            n_clusters = len(profiles_raw)
            money_label = humanize_column_name(money_col)
            if rank == 0:
                offer = f"Highest {money_label} segment — a loyalty/VIP retention offer protects this group's value."
            elif rank == n_clusters - 1:
                offer = f"Lowest {money_label} segment — a win-back or re-engagement offer may lift activity here."
            else:
                offer = (
                    f"Mid-tier {money_label} segment — a targeted upsell offer aimed at their strongest "
                    "distinguishing trait may grow this group."
                )
        elif deviations:
            top = deviations[0]
            direction = "high" if top["pct_diff"] > 0 else "low"
            offer = f"Distinguished by {direction} {top['label'].lower()} — target offers/communication around this trait for the best response."
        else:
            offer = "No behavioral feature stands out enough from the overall average to suggest a targeted offer."

        descriptive_numeric = []
        descriptive_categorical = []
        for key, val in (c.get("descriptive_summary") or {}).items():
            if isinstance(val, dict):
                top_value = max(val, key=val.get) if val else None
                descriptive_categorical.append(
                    {
                        "label": humanize_column_name(key),
                        "top_value": top_value,
                        "top_pct": val.get(top_value) if top_value is not None else None,
                    }
                )
            else:
                descriptive_numeric.append({"label": humanize_column_name(key), "value": _fmt_num(val)})

        profiles.append(
            {
                "cluster": c["cluster"],
                "name": segment_name,
                "size": c["size"],
                "pct": c["pct"],
                "distinguishing_features": deviations[:5],
                "feature_means": [{"label": d["label"], "value": d["value"]} for d in deviations],
                "descriptive_numeric": descriptive_numeric,
                "descriptive_categorical": descriptive_categorical,
                "suggested_offer": offer,
            }
        )

    return {
        "method_label": "K-Means",
        "k": clusters.get("k"),
        "silhouette_score": clusters.get("silhouette_score"),
        "features_used": [humanize_column_name(n) for n in clusters.get("features_used", [])],
        "rows_clustered": clusters.get("rows_clustered"),
        "profiles": profiles,
    }


def _build_transaction_analysis_section(ta: dict) -> dict:
    """Humanizes transaction_analysis_service's output for the report — revenue KPIs,
    top-N breakdowns (already sorted/limited by the service), RFM segments, the return-
    prediction summary, and the forecast table with its naive-baseline comparison."""
    section: dict = {"recommendations": ta.get("recommendations") or []}

    if ta.get("revenue_analytics"):
        ra = ta["revenue_analytics"]
        section["revenue"] = {
            **ra,
            "revenue_by_category": [{"label": r["label"], "revenue": r["revenue"]} for r in ra.get("revenue_by_category", [])[:10]],
            "revenue_by_item": [{"label": r["label"], "revenue": r["revenue"]} for r in ra.get("revenue_by_item", [])[:10]],
            "revenue_by_branch": [{"label": r["label"], "revenue": r["revenue"]} for r in ra.get("revenue_by_branch", [])[:10]],
        }
        if ra.get("revenue_by_month"):
            section["revenue_chart"] = report_charts.bar_chart(
                [m["month"] for m in ra["revenue_by_month"]],
                [m["revenue"] for m in ra["revenue_by_month"]],
                "Revenue by month",
            )

    if ta.get("customer_rfm"):
        section["rfm"] = ta["customer_rfm"]

    if ta.get("return_prediction"):
        rp = ta["return_prediction"]
        section["return_prediction"] = {
            "window_days": rp["window_days"],
            "total_customers": rp["total_customers"],
            "predicted_will_return": rp["predicted_will_return"],
            "predicted_will_not_return": rp["predicted_will_not_return"],
            "predicted_uncertain": rp["predicted_uncertain"],
            "risk_group_counts": rp["risk_group_counts"],
            "risk_bands": rp["risk_bands"],
            "holdout_accuracy": rp["holdout_metrics"].get("accuracy"),
        }

    if ta.get("sales_forecasting"):
        fc = ta["sales_forecasting"]
        backtest_rows = [
            {"label": s["label"], "mape": s["mape"], "folds": s["folds"], "is_winner": name == fc["method"]}
            for name, s in fc["backtest_scores"].items()
        ]
        backtest_rows.sort(key=lambda r: (r["mape"] is None, r["mape"] if r["mape"] is not None else 0))
        section["forecast"] = {
            **fc,
            "backtest_rows": backtest_rows,
        }
        section["forecast_chart"] = report_charts.bar_chart(
            [f["month"] for f in fc["monthly_forecast"]],
            [f["point_forecast"] for f in fc["monthly_forecast"]],
            "Revenue forecast (next 3 months, from a weekly model)",
        )

    return section


def build_report_context(
    db: Session,
    project: Project,
    dataset: Dataset | None,
    model: MLModel | None,
    title: str | None,
    prepared_by: str | None,
    clusters: dict | None = None,
    no_model_reason: str | None = None,
    transaction_analysis: dict | None = None,
) -> dict:
    context: dict = {
        "meta": {
            "project_name": project.name,
            "dataset_name": dataset.file_name if dataset else None,
            "analysis_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "prepared_by": prepared_by or "AI Data Scientist Platform",
            "title": title or f"{project.name} — Analytics Report",
        },
        "dataset": None,
        "data_quality": None,
        "eda": None,
        "model": None,
        "clusters": None,
        "transaction_analysis": None,
        "no_model_reason": no_model_reason,
        "feature_importance": [],
        "future_outlook": None,
        "model_recommendations": [],
        "ai_insights": None,
        "limitations": [],
        "appendix": {},
        "currency": _detect_report_currency(dataset),
    }

    if dataset is not None:
        context["dataset"] = _build_dataset_section(dataset, project)
        context["data_quality"] = _build_data_quality_section(dataset)
        try:
            context["eda"] = _build_eda_section(db, dataset)
        except Exception:
            context["eda"] = None

    if transaction_analysis is not None:
        context["transaction_analysis"] = _build_transaction_analysis_section(transaction_analysis)

    if clusters is not None and dataset is not None:
        context["clusters"] = _build_clusters_section(clusters, dataset)
        context["clusters"]["size_chart"] = report_charts.bar_chart(
            [f"Cluster {p['cluster']}" for p in context["clusters"]["profiles"]],
            [p["size"] for p in context["clusters"]["profiles"]],
            "Rows per cluster",
        )

    if model is not None and dataset is not None:
        context["model"] = _build_model_section(model, dataset)
        context["feature_importance"] = _build_feature_importance(model, dataset)
        if context["feature_importance"]:
            context["feature_importance_chart"] = report_charts.bar_chart(
                [f["label"] for f in context["feature_importance"]],
                [f["importance"] for f in context["feature_importance"]],
                "Top features driving the outcome",
            )
        context["future_outlook"] = _build_future_outlook(db, model, dataset, context["feature_importance"])
        context["model_recommendations"] = _build_model_recommendations(
            model, context["future_outlook"], context["feature_importance"]
        )
        context["appendix"] = {
            "preprocessing": model.preprocessing_json or {},
            "hyperparameters": model.hyperparameters_json or {},
            "feature_schema": [
                {**f, "label": humanize_feature_name(f["name"], _known_columns(dataset))}
                for f in (model.feature_schema_json or [])
            ],
            "random_seed": model.random_seed,
            "model_version": model.version,
        }

    context["limitations"] = _build_limitations(dataset, model) if dataset else []
    if clusters is not None:
        context["limitations"].append(
            "These groups come from unsupervised clustering (no target column was available), so they describe "
            "similarity patterns in the data, not a prediction — validate any business interpretation with domain "
            "expertise before acting on it."
        )

    # A report must always state SOME business question, not just a page count — derived
    # from what the plan actually decided to analyze, NEVER the project description (a
    # free-text field the user may not have filled in, or may have written before the
    # actual analysis path was known — it can silently mismatch what the report contains).
    if context["transaction_analysis"]:
        context["business_question"] = (
            "How is the business performing (revenue, refunds), who are the best customers, which "
            "customers are likely to return, and what should revenue look like in the coming months?"
        )
    elif context["model"]:
        context["business_question"] = f"What predicts {context['model']['target_label']}, and how reliably?"
    elif context["clusters"]:
        context["business_question"] = "What natural groupings exist in this data, and what distinguishes them?"
    elif context["dataset"]:
        context["business_question"] = "What does this dataset contain, and what does it reveal on first analysis?"
    else:
        context["business_question"] = None

    return context
