"""Predicts whether each CURRENT customer will purchase again within the next N days.

Strictly time-based methodology, to avoid future data leaking into features:
  1. TRAINING set: pick a cutoff date in the PAST (data_end - window_days). Features for
     each customer are built from ONLY their transactions on/before that cutoff. The label
     ("returned") is computed from transactions strictly AFTER the cutoff, within the next
     `window_days` — real historical outcomes, not future information disguised as a
     feature.
  2. PREDICTION: cutoff = the dataset's most recent date (i.e. "now"). Features for every
     currently-active customer are built from ALL their history up to now. The model
     (trained only on step 1's historical, verifiable examples) predicts forward into an
     unknowable future — no leakage is possible here by construction, since the days being
     predicted don't exist in the training data at all.
"""

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.services import ml_service
from app.services.customer_analytics_service import pick_column, build_customer_table
from app.utils.metrics import classification_metrics

RETURN_WINDOW_DAYS = 90
NUMERIC_FEATURES = ["recency_days", "frequency", "monetary", "refund_total", "loyalty_points_used"]
CATEGORICAL_FEATURES = ["favorite_branch", "favorite_category"]

# Single source of truth for both the risk-group labels AND the headline "predicted to
# return" count — previously these used two different thresholds (the classifier's own
# 0.5 decision boundary for the headline, vs. 0.6/0.35 for the risk bands), so the
# headline number didn't match the "Likely to return" band it was supposed to summarize.
# Now the headline IS the "Likely to return" band count, by construction, so they can
# never disagree.
RISK_BAND_LIKELY = 0.6
RISK_BAND_AT_RISK = 0.4
RISK_GROUP_LIKELY = "Likely to return"
RISK_GROUP_UNCERTAIN = "Uncertain"
RISK_GROUP_AT_RISK = "At risk of not returning"


def _risk_group(p: float) -> str:
    if p >= RISK_BAND_LIKELY:
        return RISK_GROUP_LIKELY
    if p >= RISK_BAND_AT_RISK:
        return RISK_GROUP_UNCERTAIN
    return RISK_GROUP_AT_RISK


def _customer_features_and_label(
    df: pd.DataFrame, column_roles: dict[str, dict], cutoff_date: pd.Timestamp, window_days: int
) -> pd.DataFrame:
    customer_col = pick_column(column_roles, "customer_id")
    timestamp_col = pick_column(column_roles, "timestamp")
    work = df.copy()
    work[timestamp_col] = pd.to_datetime(work[timestamp_col], errors="coerce")

    past = work[work[timestamp_col] <= cutoff_date]
    features_df = build_customer_table(past, column_roles, as_of_date=cutoff_date)
    if features_df.empty:
        return features_df

    future_end = cutoff_date + pd.Timedelta(days=window_days)
    future_window = work[(work[timestamp_col] > cutoff_date) & (work[timestamp_col] <= future_end)]
    returning = set(future_window[customer_col].dropna().unique())
    features_df["returned"] = features_df["customer_id"].isin(returning).astype(int)
    return features_df


def _build_pipeline(numeric_features: list[str], categorical_features: list[str], random_seed: int = 42) -> Pipeline:
    transformers = []
    if numeric_features:
        transformers.append(
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]), numeric_features)
        )
    if categorical_features:
        transformers.append(
            (
                "cat",
                Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("encoder", OneHotEncoder(handle_unknown="ignore"))]),
                categorical_features,
            )
        )
    preprocessor = ColumnTransformer(transformers=transformers)
    model = RandomForestClassifier(n_estimators=200, random_state=random_seed, class_weight="balanced")
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])


def run_return_prediction(
    df: pd.DataFrame,
    column_roles: dict[str, dict],
    window_days: int = RETURN_WINDOW_DAYS,
    random_seed: int = 42,
) -> dict | None:
    """Returns a full result dict, or None if there isn't enough historical data to both
    train (needs data before the training cutoff) AND validate (needs `window_days` of
    real data after it) — never fabricates a result from insufficient history."""
    timestamp_col = pick_column(column_roles, "timestamp")
    customer_col = pick_column(column_roles, "customer_id")
    if not timestamp_col or not customer_col:
        return None

    parsed = pd.to_datetime(df[timestamp_col], errors="coerce")
    data_start, data_end = parsed.min(), parsed.max()
    if pd.isna(data_start) or pd.isna(data_end):
        return None

    train_cutoff = data_end - pd.Timedelta(days=window_days)
    if train_cutoff <= data_start:
        return None  # not enough history to both build features and validate a full window

    training_set = _customer_features_and_label(df, column_roles, train_cutoff, window_days)
    if len(training_set) < 30 or training_set["returned"].nunique() < 2:
        return None  # too few historical examples, or a degenerate single-class label

    # Phase 5 (modeling safety unification, Finding 2): this pipeline used to build its
    # own model with zero leakage checking, no baseline comparison, and no weak-model
    # flag — entirely separate from ml_service.train_models()'s real safety mechanisms.
    # Rather than rebuilding this pipeline on top of train_models() (a genuinely different
    # data shape: a time-cutoff train/predict split over customer-level aggregates from
    # build_customer_table(), not a raw dataset row-per-record), this reuses the SAME
    # shared detection functions ml_service.train_models() calls, verified against real
    # data (coffee_shop_transactions.csv) rather than assumed safe just because the
    # feature list is small and hand-picked.
    all_feature_cols = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    leakage_warnings = ml_service.detect_leakage(training_set, "returned", all_feature_cols)
    leakage_warnings += ml_service.detect_pairwise_leakage(training_set, "returned", all_feature_cols)
    leakage_warnings += ml_service.detect_derived_metric_features(training_set, "returned", all_feature_cols)
    leaked_names = {w["column"] for w in leakage_warnings}
    numeric_features = [c for c in NUMERIC_FEATURES if c not in leaked_names]
    categorical_features = [c for c in CATEGORICAL_FEATURES if c not in leaked_names]
    feature_cols = numeric_features + categorical_features
    if not feature_cols:
        return None  # every candidate feature was excluded as likely leakage — never train on it

    X = training_set[feature_cols]
    y = training_set["returned"]

    stratify = y if y.nunique() > 1 else None
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=random_seed, stratify=stratify)

    pipeline = _build_pipeline(numeric_features, categorical_features, random_seed)
    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test) if hasattr(pipeline, "predict_proba") else None
    holdout_metrics = classification_metrics(y_test, y_pred, y_proba)

    cv_scores = []
    try:
        effective_cv = max(2, min(5, y_train.value_counts().min()))
        cv_scores = cross_val_score(pipeline, X_train, y_train, cv=effective_cv, scoring="f1_weighted").tolist()
    except Exception:
        cv_scores = []
    model_score = float(holdout_metrics.get("f1", 0) or 0)
    baseline_comparison = ml_service.compute_baseline_comparison("classification", y_train, y_test, model_score, cv_scores)
    is_weak, weak_reason = ml_service.evaluate_model_strength(holdout_metrics, baseline_comparison)

    # Forward prediction: cutoff = the dataset's own most recent date ("now"), features
    # from ALL history — this is what actually gets reported to the user.
    predict_set = _customer_features_and_label(df, column_roles, data_end, window_days=0)
    predict_set = predict_set.drop(columns=["returned"], errors="ignore")
    X_predict = predict_set[feature_cols]
    predicted = pipeline.predict(X_predict)
    predicted_proba = pipeline.predict_proba(X_predict)[:, 1] if hasattr(pipeline, "predict_proba") else predicted.astype(float)

    predict_set = predict_set.copy()
    predict_set["return_probability"] = np.round(predicted_proba, 4)
    predict_set["risk_group"] = predict_set["return_probability"].map(_risk_group)
    # "predicted_return" now means exactly "falls in the Likely-to-return band" — the
    # SAME test that produces risk_group — rather than the classifier's separate raw 0.5
    # decision boundary, so this per-customer flag and the risk group can never disagree.
    predict_set["predicted_return"] = predict_set["risk_group"] == RISK_GROUP_LIKELY

    total_customers = int(len(predict_set))
    risk_group_counts = predict_set["risk_group"].value_counts().to_dict()
    will_return = int(risk_group_counts.get(RISK_GROUP_LIKELY, 0))
    will_not_return = int(risk_group_counts.get(RISK_GROUP_AT_RISK, 0))
    uncertain = int(risk_group_counts.get(RISK_GROUP_UNCERTAIN, 0))
    assert will_return + will_not_return + uncertain == total_customers

    feature_importances = None
    try:
        model = pipeline.named_steps["model"]
        preproc = pipeline.named_steps["preprocessor"]
        cat_names = (
            list(preproc.named_transformers_["cat"].named_steps["encoder"].get_feature_names_out(categorical_features))
            if categorical_features
            else []
        )
        names = numeric_features + cat_names
        importances = model.feature_importances_
        pairs = sorted(zip(names, importances), key=lambda x: x[1], reverse=True)[:10]
        total = sum(v for _, v in pairs) or 1.0
        feature_importances = [{"name": n, "importance": round(float(v) / total, 4)} for n, v in pairs]
    except Exception:
        feature_importances = []

    return {
        "window_days": window_days,
        "train_cutoff": train_cutoff.strftime("%Y-%m-%d"),
        "predict_cutoff": data_end.strftime("%Y-%m-%d"),
        "training_customers": int(len(training_set)),
        "holdout_metrics": holdout_metrics,
        "feature_importance": feature_importances,
        # Phase 5 — the same safety signals ml_service.train_models() reports, applied
        # here for the first time: which candidate features (if any) were excluded as
        # likely leakage, how the model compares to a trivial baseline, and whether it's
        # flagged as too weak to be a real predictor.
        "features_used": feature_cols,
        "leakage_warnings": leakage_warnings,
        "baseline": baseline_comparison,
        "is_weak": is_weak,
        "weak_reason": weak_reason,
        "total_customers": total_customers,
        # The headline count IS the "Likely to return" band count — no separate
        # threshold, so this can never disagree with risk_group_counts below.
        "predicted_will_return": will_return,
        "predicted_will_not_return": will_not_return,
        "predicted_uncertain": uncertain,
        "risk_bands": {
            RISK_GROUP_LIKELY: f">= {int(RISK_BAND_LIKELY * 100)}% predicted probability",
            RISK_GROUP_UNCERTAIN: f"{int(RISK_BAND_AT_RISK * 100)}%-{int(RISK_BAND_LIKELY * 100)}% predicted probability",
            RISK_GROUP_AT_RISK: f"< {int(RISK_BAND_AT_RISK * 100)}% predicted probability",
        },
        "risk_group_counts": risk_group_counts,
        "predictions": predict_set[["customer_id", "predicted_return", "return_probability", "risk_group"]].to_dict("records"),
    }
