import os
import uuid

import joblib
import numpy as np
import pandas as pd
from scipy.stats import skew as _scipy_skew
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Lasso, LogisticRegression, Ridge
from sklearn.metrics import adjusted_rand_score, confusion_matrix, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.svm import SVC, SVR
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sqlalchemy.orm import Session
from xgboost import XGBClassifier, XGBRegressor

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.ml_model import MLModel
from app.services.data_understanding_service import (
    AGE_NAME_TOKEN,
    ROLE_CATEGORY,
    ROLE_CUSTOMER_ID,
    ROLE_IDENTIFIER,
    ROLE_TIMESTAMP,
    classify_columns,
    detect_dataset_type,
    detect_post_outcome_columns,
)
from app.services.dataset_service import (
    build_dataset_profile,
    detect_semantic_type,
    is_id_like_column,
    load_dataframe,
    tokenize_column_name,
    tokens_match_any,
)
from app.utils.metrics import classification_metrics, regression_metrics
from app.utils.validators import DatasetValidationError

# Every lambda takes (seed, class_weight=None) for uniform calling code in train_models,
# even though several algorithms (KNN, gradient boosting, XGBoost, SVR) have no sklearn
# class_weight concept — those simply ignore the argument rather than erroring. See
# _classification_needs_balancing() for when a non-None value is actually passed.
CLASSIFICATION_MODELS = {
    "logistic_regression": lambda seed, class_weight=None: LogisticRegression(
        max_iter=1000, random_state=seed, class_weight=class_weight
    ),
    "decision_tree": lambda seed, class_weight=None: DecisionTreeClassifier(random_state=seed, class_weight=class_weight),
    "random_forest": lambda seed, class_weight=None: RandomForestClassifier(
        n_estimators=200, random_state=seed, class_weight=class_weight
    ),
    "extra_trees": lambda seed, class_weight=None: ExtraTreesClassifier(
        n_estimators=200, random_state=seed, class_weight=class_weight
    ),
    "gradient_boosting": lambda seed, class_weight=None: GradientBoostingClassifier(random_state=seed),
    "xgboost": lambda seed, class_weight=None: XGBClassifier(random_state=seed, eval_metric="logloss"),
    "knn": lambda seed, class_weight=None: KNeighborsClassifier(n_neighbors=5),
    "svm": lambda seed, class_weight=None: SVC(random_state=seed, probability=True, class_weight=class_weight),
}

REGRESSION_MODELS = {
    "linear_regression": lambda seed, class_weight=None: Ridge(alpha=0.0001, random_state=seed),
    "ridge_regression": lambda seed, class_weight=None: Ridge(alpha=1.0, random_state=seed),
    "lasso_regression": lambda seed, class_weight=None: Lasso(alpha=0.001, random_state=seed),
    "decision_tree": lambda seed, class_weight=None: DecisionTreeRegressor(random_state=seed),
    "random_forest": lambda seed, class_weight=None: RandomForestRegressor(n_estimators=200, random_state=seed),
    "extra_trees": lambda seed, class_weight=None: ExtraTreesRegressor(n_estimators=200, random_state=seed),
    "gradient_boosting": lambda seed, class_weight=None: GradientBoostingRegressor(random_state=seed),
    "xgboost": lambda seed, class_weight=None: XGBRegressor(random_state=seed),
    "svm": lambda seed, class_weight=None: SVR(),
}

MODEL_LABELS = {
    "logistic_regression": "Logistic Regression",
    "decision_tree": "Decision Tree",
    "random_forest": "Random Forest",
    "extra_trees": "Extra Trees",
    "gradient_boosting": "Gradient Boosting",
    "xgboost": "XGBoost",
    "knn": "K-Nearest Neighbors",
    "svm": "Support Vector Machine",
    "linear_regression": "Linear Regression",
    "ridge_regression": "Ridge Regression",
    "lasso_regression": "Lasso Regression",
}


# SVM scales poorly (O(n^2)-O(n^3) in row count) and KNN's distance computation gets slow
# at scale — both are skipped past these thresholds ONLY on the automatic path (Auto
# Analyze, model_keys=None). Manual selection via the Modeling tab's checkboxes is never
# restricted by this — a user can always explicitly opt into SVM/KNN on any size dataset.
_SVM_ROW_LIMIT = 5000
_KNN_ROW_LIMIT = 20000


def recommend_model_keys(problem_type: str, n_rows: int) -> list[str]:
    """Which registered models are appropriate to auto-train for this dataset size —
    spec requirement: 'Do NOT train every algorithm blindly... determine which models are
    appropriate for dataset size, task, feature types, target characteristics.'"""
    candidates = CLASSIFICATION_MODELS if problem_type == "classification" else REGRESSION_MODELS
    keys = list(candidates.keys())
    if n_rows > _SVM_ROW_LIMIT:
        keys = [k for k in keys if k != "svm"]
    if n_rows > _KNN_ROW_LIMIT:
        keys = [k for k in keys if k != "knn"]
    return keys


def _classification_class_weight(y_train: pd.Series) -> str | None:
    """Detects real class imbalance (spec: 'Handle class imbalance') and returns the
    sklearn class_weight value to use, or None when classes are already reasonably
    balanced. Threshold of 3:1 majority:minority is a common rule-of-thumb cutoff, not an
    arbitrary guess — below it, class_weight='balanced' tends to do more harm than good by
    over-correcting on noise."""
    counts = y_train.value_counts()
    if len(counts) < 2:
        return None
    ratio = counts.iloc[0] / counts.iloc[-1]
    return "balanced" if ratio >= 3 else None


def detect_problem_type(series: pd.Series) -> str:
    clean = series.dropna()
    if pd.api.types.is_bool_dtype(clean):
        return "classification"
    if pd.api.types.is_numeric_dtype(clean):
        nunique = clean.nunique()
        if nunique <= max(10, int(0.02 * len(clean))) and nunique <= 20:
            return "classification"
        return "regression"
    return "classification"


def _build_preprocessor(numeric_features: list[str], categorical_features: list[str]) -> ColumnTransformer:
    numeric_pipeline = Pipeline(
        steps=[("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]
    )
    categorical_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoder", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("num", numeric_pipeline, numeric_features),
            ("cat", categorical_pipeline, categorical_features),
        ]
    )


def _feature_importance(pipeline: Pipeline, numeric_features: list[str], categorical_features: list[str]) -> list[dict]:
    model = pipeline.named_steps["model"]
    preprocessor = pipeline.named_steps["preprocessor"]

    feature_names = list(numeric_features)
    if categorical_features:
        try:
            cat_encoder = preprocessor.named_transformers_["cat"].named_steps["encoder"]
            feature_names += list(cat_encoder.get_feature_names_out(categorical_features))
        except Exception:
            feature_names += categorical_features

    importances = None
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
    elif hasattr(model, "coef_"):
        coef = model.coef_
        importances = np.abs(coef[0]) if coef.ndim > 1 else np.abs(coef)

    if importances is None or len(importances) != len(feature_names):
        return []

    pairs = sorted(zip(feature_names, (float(v) for v in importances)), key=lambda x: x[1], reverse=True)
    total = sum(abs(v) for _, v in pairs) or 1.0
    return [
        {"name": str(name), "importance": round(value / total, 4)}
        for name, value in pairs[:20]
    ]


# How close a ROC-AUC has to be to 0.5 (no discrimination at all) to count as "the model
# isn't really predicting anything" — a fixed, conventional band (this is a widely-used ML
# convention, not an ad-hoc guess, unlike the baseline comparison which deliberately avoids
# a fixed number). A weighted multi-class ROC-AUC in this band means the model is
# statistically indistinguishable from random guessing, even if it "clearly beats" a naive
# baseline on F1 alone (which can happen on an imbalanced multi-class target).
WEAK_MODEL_ROC_AUC_BAND = 0.05


def evaluate_model_strength(metrics: dict, baseline_comparison: dict) -> tuple[bool, str | None]:
    """Returns (is_weak, reason). A model is weak if it doesn't clearly beat a trivial
    baseline, OR its ROC-AUC is close to 0.5 (classification only) — the latter catches a
    model that looks fine by the baseline/F1 comparison but has no real discriminative
    power, which is exactly what let a garbage target (e.g. a 5-city grouping column)
    silently earn a green checkmark before."""
    if not baseline_comparison.get("clearly_beats_baseline", True):
        return True, (
            f"This model barely beats a simple baseline ({baseline_comparison.get('baseline_metric')}="
            f"{baseline_comparison.get('baseline_score')} vs. model={baseline_comparison.get('model_score')})."
        )
    roc_auc = metrics.get("roc_auc")
    if roc_auc is not None and abs(float(roc_auc) - 0.5) <= WEAK_MODEL_ROC_AUC_BAND:
        return True, (
            f"ROC-AUC ({round(float(roc_auc), 4)}) is close to 0.5 — the model is not much "
            "better than random guessing."
        )
    return False, None


LEAKAGE_CORRELATION_THRESHOLD = 0.95


class FeatureSelection:
    def __init__(self):
        self.features: list[str] = []
        self.excluded_id_like: list[str] = []
        self.excluded_datetime_raw: list[str] = []
        self.derived_date_features: list[str] = []


def select_training_features(df: pd.DataFrame, target_column: str) -> tuple[pd.DataFrame, FeatureSelection]:
    """Auto-selects features for training when the caller doesn't specify them explicitly.

    Excludes ID-like columns entirely (pure noise), and instead of dropping raw date
    columns outright, extracts year/month/day-of-week from them — genuinely useful signal
    (seasonality, day-of-week effects) that a raw date string can't offer a model directly.

    A raw CSV/Excel read never parses dates into real datetime64 dtype on its own, so this
    must reuse the same semantic-type detection the dataset profile already relies on
    (name hints + a parse attempt) rather than checking pandas dtype directly — otherwise
    date columns like "signup_date" silently slip through as free-text categoricals.
    """
    df = df.copy()
    selection = FeatureSelection()

    for col in df.columns:
        if col == target_column:
            continue
        series = df[col]
        semantic = detect_semantic_type(series, str(col))

        if semantic == "datetime":
            selection.excluded_datetime_raw.append(str(col))
            parsed = pd.to_datetime(series, errors="coerce")
            if parsed.notna().sum() > 0:
                for part, accessor in (("year", "year"), ("month", "month"), ("dayofweek", "dayofweek")):
                    derived_name = f"{col}_{part}"
                    df[derived_name] = getattr(parsed.dt, accessor)
                    selection.derived_date_features.append(derived_name)
                    selection.features.append(derived_name)
            continue

        unique = int(series.nunique(dropna=True))
        is_numeric = semantic == "numeric"
        if is_id_like_column(str(col), unique, len(df), is_numeric):
            selection.excluded_id_like.append(str(col))
            continue

        selection.features.append(col)

    return df, selection


def derive_date_features_for_prediction(df: pd.DataFrame, preprocessing: dict) -> pd.DataFrame:
    """The inverse of select_training_features's date handling, used at prediction time:
    reconstructs the year/month/dayofweek columns a model was trained on from the ORIGINAL
    raw date column(s) (preprocessing['excluded_datetime_raw']) a fresh upload naturally
    contains, so prediction — batch or single-row — only ever requires the raw columns a
    real user has (e.g. 'signup_date'), never the model's internal derived feature names
    ('signup_date_year', ...). A no-op for any raw column not present in df (missing_cols
    validation downstream reports that honestly rather than this silently guessing)."""
    df = df.copy()
    excluded_datetime_raw = preprocessing.get("excluded_datetime_raw") or []
    derived_date_features = set(preprocessing.get("derived_date_features") or [])
    for raw_col in excluded_datetime_raw:
        if raw_col not in df.columns:
            continue
        parsed = pd.to_datetime(df[raw_col], errors="coerce")
        for part, accessor in (("year", "year"), ("month", "month"), ("dayofweek", "dayofweek")):
            derived_name = f"{raw_col}_{part}"
            if derived_name in derived_date_features:
                df[derived_name] = getattr(parsed.dt, accessor)
    return df


SINGLE_THRESHOLD_LEAKAGE_AGREEMENT = 0.97


def _best_single_threshold_agreement(feature: pd.Series, target_bool: pd.Series) -> float:
    """Same 'does a > b agree with the target' idea detect_pairwise_leakage uses between
    TWO columns, applied to one column against a scanned split threshold instead — catches
    a deterministic-but-nonlinear single-column leak (e.g. a future-window event COUNT that
    is exactly 0 whenever the outcome is negative and >0 whenever it's positive) that plain
    Pearson correlation can under-score, since the count's extra graded variation among the
    positive cases dilutes the linear correlation even though the threshold split is exact.
    Skipped for very high-cardinality features (>1000 distinct values): a real deterministic
    leak of this kind is inherently a small, reused set of values (a count, a tier, a flag),
    not a near-continuous measurement, so this stays cheap without needing a result cap."""
    values = feature.dropna().unique()
    if len(values) < 2 or len(values) > 1000:
        return 0.0
    values = np.sort(values)
    thresholds = (values[:-1] + values[1:]) / 2.0
    mask = feature.notna()
    aligned_feature = feature[mask].to_numpy()
    aligned_target = target_bool[mask].to_numpy()
    best = 0.0
    for t in thresholds:
        derived = aligned_feature > t
        agreement = (derived == aligned_target).mean()
        agreement = max(agreement, 1 - agreement)
        best = max(best, agreement)
    return best


def detect_leakage(df: pd.DataFrame, target_column: str, feature_columns: list[str]) -> list[dict]:
    """Flags features that are near-copies or direct calculations of the target, based on
    a statistically meaningful signal — either a near-perfect linear correlation (>= 0.95)
    or, for a binary target, a near-perfect single-threshold split agreement (>= 0.97) —
    rather than a name-based guess, so a feature genuinely derived from the target (e.g.
    profit as a fixed fraction of sales_amount, or a future-window order count that's
    exactly 0 whenever the target is negative) doesn't let the model 'cheat' by learning to
    reconstruct the target from a disguised copy of itself.
    """
    target_series = pd.to_numeric(df[target_column], errors="coerce")
    if target_series.notna().sum() < 2:
        target_series = df[target_column].astype("category").cat.codes.astype(float)
        target_series = target_series.replace(-1, np.nan)

    target_codes, target_uniques = pd.factorize(df[target_column].astype(str))
    target_bool = pd.Series(target_codes.astype(bool), index=df.index) if len(target_uniques) == 2 else None

    warnings = []
    for col in feature_columns:
        if col not in df.columns:
            continue
        feature_series = pd.to_numeric(df[col], errors="coerce")
        feature_bool = None
        if feature_series.notna().sum() < 2:
            # A binary categorical feature (e.g. 'late_delivery': Yes/No) coerces to
            # all-NaN and would otherwise be invisible to every check below, even when
            # it's structurally derived from a NUMERIC target — same factorize-as-0/1
            # fallback _abs_correlation already uses for this exact shape.
            non_null = df[col].dropna()
            if non_null.nunique() == 2:
                codes, _ = pd.factorize(df[col].astype(str))
                feature_bool = pd.Series(codes.astype(bool), index=df.index).where(df[col].notna())
                feature_series = feature_bool.astype(float)
            else:
                continue
        aligned = pd.concat([feature_series, target_series], axis=1).dropna()
        corr = None
        if len(aligned) >= 5:
            try:
                corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            except Exception:
                corr = None
            if corr != corr:  # NaN check without importing math
                corr = None

        if corr is not None and abs(corr) >= LEAKAGE_CORRELATION_THRESHOLD:
            warnings.append(
                {
                    "column": col,
                    "correlation": round(corr, 4),
                    "reason": (
                        f"Nearly identical to the target ({abs(corr):.0%} correlated) — likely a direct "
                        f"calculation or duplicate of '{target_column}', which would let the model 'cheat' "
                        "by reconstructing the target instead of learning real patterns."
                    ),
                }
            )
            continue

        if target_bool is not None:
            agreement = _best_single_threshold_agreement(feature_series, target_bool)
            if agreement >= SINGLE_THRESHOLD_LEAKAGE_AGREEMENT:
                warnings.append(
                    {
                        "column": col,
                        "correlation": round(agreement, 4),
                        "reason": (
                            f"A single threshold split of '{col}' agrees with '{target_column}' "
                            f"{agreement:.1%} of the time — a near-perfect, deterministic relationship "
                            "(e.g. a future-window count that's exactly zero whenever the outcome is "
                            "negative), even where its plain linear correlation looked unremarkable."
                        ),
                    }
                )
                continue

        if feature_bool is not None:
            # A binary/categorical feature might be DEFINED by comparing the (numeric)
            # TARGET against ANOTHER feature (e.g. 'late_delivery' = delivery_time_min >
            # promised_time_min, where delivery_time_min IS the target here) — the same
            # 'does a > b agree with a boolean' technique detect_pairwise_leakage uses
            # between two features, applied between the target and one feature instead.
            best_agreement, best_other = 0.0, None
            for other in feature_columns:
                if other == col or other not in df.columns:
                    continue
                other_numeric = pd.to_numeric(df[other], errors="coerce")
                mask = other_numeric.notna() & target_series.notna() & feature_bool.notna()
                if mask.sum() < 10:
                    continue
                derived = target_series[mask] > other_numeric[mask]
                agreement = (derived.values == feature_bool[mask].values).mean()
                agreement = max(agreement, 1 - agreement)
                if agreement > best_agreement:
                    best_agreement, best_other = agreement, other
            if best_agreement >= PAIRWISE_LEAKAGE_AGREEMENT_THRESHOLD:
                warnings.append(
                    {
                        "column": col,
                        "correlation": round(best_agreement, 4),
                        "reason": (
                            f"'{col}' agrees with comparing the target '{target_column}' against "
                            f"'{best_other}' {best_agreement:.1%} of the time (e.g. {col} is defined as "
                            f"{target_column} > {best_other}) — a direct structural derivation of the "
                            "target, not a genuine predictive input."
                        ),
                    }
                )
    return warnings


PAIRWISE_LEAKAGE_AGREEMENT_THRESHOLD = 0.95


PAIRWISE_CARDINALITY_ASYMMETRY_RATIO = 3.0


def _outcome_side_of_pair(va: pd.Series, vb: pd.Series) -> str | None:
    """Given two numeric series whose comparison leaks the target, determines which one
    is more likely the POST-OUTCOME (realized/continuous) side vs. the HISTORY (planned/
    committed) side, using a data-evidence signal — never a column name: a planned/
    promised/SLA-style value is drawn from a small, reused set (e.g. a delivery's
    PROMISED time is one of a handful of standard SLA tiers), while the corresponding
    REALIZED outcome varies almost continuously. Returns "a" or "b" (whichever is the
    outcome side, to exclude), or None when the two columns' cardinality is too similar
    to tell confidently — in that ambiguous case the caller falls back to excluding both,
    the previous (safe but overly broad) behavior."""
    unique_a, unique_b = va.nunique(), vb.nunique()
    if unique_a == 0 or unique_b == 0 or unique_a == unique_b:
        return None
    ratio = max(unique_a, unique_b) / min(unique_a, unique_b)
    if ratio < PAIRWISE_CARDINALITY_ASYMMETRY_RATIO:
        return None
    return "a" if unique_a > unique_b else "b"


def detect_pairwise_leakage(df: pd.DataFrame, target_column: str, feature_columns: list[str]) -> list[dict]:
    """Flags PAIRS of numeric features whose simple comparison (a > b) almost exactly
    reproduces a binary target — e.g. 'late_delivery' being literally defined as
    'delivery_time_min > promised_time_min'. Single-column correlation can't see this kind
    of leak, since neither column alone is highly correlated with the target; only the
    comparison between them is. Only applies when the target is binary (a continuous or
    multi-class target has no single natural "a > b" analogue to test against).

    A leaking pair is NOT excluded symmetrically by default — see _outcome_side_of_pair:
    when one side is confidently identifiable as the realized-outcome side (e.g.
    delivery_time_min) and the other as a planned/committed value known in advance (e.g.
    promised_time_min), only the outcome side is excluded. The history side is real,
    legitimate, available-before-prediction information and must not be thrown away just
    because it happens to correlate with the target alongside its outcome counterpart.
    """
    codes, uniques = pd.factorize(df[target_column].astype(str))
    if len(uniques) != 2:
        return []
    target_bool = pd.Series(codes.astype(bool), index=df.index)

    candidates = []
    for col in feature_columns:
        if col not in df.columns:
            continue
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.notna().sum() >= 0.9 * len(df):
            candidates.append(col)

    warnings: list[dict] = []
    flagged: set[str] = set()
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if a in flagged and b in flagged:
                continue
            va = pd.to_numeric(df[a], errors="coerce")
            vb = pd.to_numeric(df[b], errors="coerce")
            mask = va.notna() & vb.notna()
            if mask.sum() < 10:
                continue
            derived = va[mask] > vb[mask]
            agreement = (derived.values == target_bool[mask].values).mean()
            agreement = max(agreement, 1 - agreement)  # covers the opposite target/code mapping
            if agreement >= PAIRWISE_LEAKAGE_AGREEMENT_THRESHOLD:
                outcome_side = _outcome_side_of_pair(va[mask], vb[mask])
                if outcome_side == "a":
                    pairs_to_flag = [(a, b, True)]
                elif outcome_side == "b":
                    pairs_to_flag = [(b, a, True)]
                else:
                    # Ambiguous which side is the realized outcome — fall back to the
                    # safe default of excluding both, same as before this distinction existed.
                    pairs_to_flag = [(a, b, False), (b, a, False)]
                for col, other, confidently_outcome_side in pairs_to_flag:
                    if col in flagged:
                        continue
                    flagged.add(col)
                    reason = (
                        f"Together with '{other}', this column almost exactly reproduces the "
                        f"target ({agreement:.1%} agreement via a simple comparison)"
                    )
                    if confidently_outcome_side:
                        reason += (
                            f" — '{col}' varies far more than '{other}' ({int(df[col].nunique())} vs "
                            f"{int(df[other].nunique())} distinct values), the signature of a realized "
                            f"outcome vs. a planned/committed value — so only '{col}' is excluded; "
                            f"'{other}' is kept as a legitimate, known-in-advance feature."
                        )
                    else:
                        reason += (
                            " — the model could 'cheat' by combining the two instead of learning real "
                            "patterns, and it isn't clear from the data alone which side is known in "
                            "advance, so both are excluded by default (re-includable manually)."
                        )
                    warnings.append({"column": col, "correlation": round(float(agreement), 4), "reason": reason})
    return warnings


def _abs_correlation(df: pd.DataFrame, col: str, target_series: pd.Series) -> float | None:
    """Correlation between a feature and the target, extended to handle a BINARY
    categorical feature (e.g. 'late_delivery': Yes/No) against a numeric target: coercing
    such a column to numeric always fails (Yes/No aren't numbers), which used to make it
    invisible to every name-hint-gated leakage check below even when it's structurally
    derived from that exact target (e.g. late_delivery = delivery_time_min > promised).
    Factorizing a 2-valued column into 0/1 and correlating is mathematically the
    point-biserial correlation — a standard, unambiguous measure for binary-vs-continuous.
    Deliberately NOT extended to 3+ category columns, where factorize's arbitrary numeric
    order would make the resulting 'correlation' statistically meaningless."""
    feature_series = pd.to_numeric(df[col], errors="coerce")
    if feature_series.notna().sum() < 2:
        non_null = df[col].dropna()
        if non_null.nunique() == 2:
            feature_series = pd.Series(pd.factorize(df[col])[0], index=df.index).astype(float)
            feature_series = feature_series.where(df[col].notna())
    aligned = pd.concat([feature_series, target_series], axis=1).dropna()
    if len(aligned) < 5:
        return None
    try:
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    except Exception:
        return None
    if corr != corr:  # NaN
        return None
    return abs(float(corr))


# Words that name a value COMPUTED FROM other monetary fields in the same row/event
# (profit = revenue - cost, margin = profit / revenue, ...) rather than an input known
# before the outcome. Unlike data_understanding_service's POST_OUTCOME_HINTS (a timing
# concept — "known only after the outcome"), this is a DERIVATION concept — these columns are typically
# calculated alongside the target from the same underlying transaction, so even a
# moderate (not near-perfect) correlation is meaningful evidence of leakage. A pure
# correlation threshold can't reliably separate this from a legitimate strong predictor
# (e.g. unit_price, an input revenue is calculated FROM, often correlates with revenue
# just as strongly as profit does) — the name match is what tips the balance here.
DERIVED_METRIC_NAME_HINTS = ("profit", "margin", "markup", "commission", "surplus")
DERIVED_METRIC_CORRELATION_FLOOR = 0.6


def detect_derived_metric_features(df: pd.DataFrame, target_column: str, feature_columns: list[str]) -> list[dict]:
    """Flags a feature as unconditional leakage when its name suggests it's a computed
    financial outcome (profit, margin, ...) AND it shows a real, non-trivial correlation
    with the target (>= DERIVED_METRIC_CORRELATION_FLOOR) — the combination of both
    signals is what makes this safe to auto-exclude without a review step, unlike the
    generic high-correlation check below, which alone is not enough evidence."""
    name_candidates = [
        c for c in feature_columns if tokens_match_any(tokenize_column_name(c), DERIVED_METRIC_NAME_HINTS)
    ]
    if not name_candidates:
        return []

    target_series = pd.to_numeric(df[target_column], errors="coerce")
    if target_series.notna().sum() < 2:
        target_series = df[target_column].astype("category").cat.codes.astype(float)
        target_series = target_series.replace(-1, np.nan)

    warnings: list[dict] = []
    for col in name_candidates:
        corr = _abs_correlation(df, col, target_series)
        if corr is None or corr < DERIVED_METRIC_CORRELATION_FLOOR:
            continue
        warnings.append(
            {
                "column": col,
                "correlation": round(corr, 4),
                "reason": (
                    f"'{col}' is named like a value computed FROM other monetary fields "
                    f"(e.g. profit = revenue - cost), and correlates with the target here "
                    f"({corr:.0%}) — likely calculated alongside '{target_column}' from the same "
                    "transaction rather than known beforehand, which would let the model 'cheat' "
                    "by learning a near-reconstruction of the target."
                ),
            }
        )
    return warnings


HIGH_CORRELATION_REVIEW_THRESHOLD = 0.75


def detect_high_correlation_features(
    df: pd.DataFrame, target_column: str, feature_columns: list[str]
) -> list[dict]:
    """Name-agnostic visibility layer: flags any RETAINED feature with a high (but not
    near-duplicate) correlation to the target for human review, without excluding it —
    a high correlation alone isn't proof of leakage (a legitimate strong predictor, e.g.
    unit_price for sales_amount, can correlate just as strongly as a real leak), so this
    never removes a feature automatically. It exists purely so a report/reviewer can see
    and double-check it, satisfying 'detect suspiciously high correlation' without risking
    an evidence-free auto-removal of a real predictor."""
    target_series = pd.to_numeric(df[target_column], errors="coerce")
    if target_series.notna().sum() < 2:
        target_series = df[target_column].astype("category").cat.codes.astype(float)
        target_series = target_series.replace(-1, np.nan)

    warnings: list[dict] = []
    for col in feature_columns:
        if col not in df.columns:
            continue
        corr = _abs_correlation(df, col, target_series)
        if corr is None or corr < HIGH_CORRELATION_REVIEW_THRESHOLD:
            continue
        warnings.append(
            {
                "column": col,
                "correlation": round(corr, 4),
                "reason": (
                    f"High correlation with the target ({corr:.0%}) — retained as a feature since "
                    "no additional evidence (a suspicious name, or a near-perfect/derived "
                    "relationship) confirms it's calculated from the target rather than a genuinely "
                    "strong, legitimate predictor. Worth a quick manual check that this value would "
                    "actually be known before predicting the outcome."
                ),
            }
        )
    return sorted(warnings, key=lambda w: w["correlation"], reverse=True)


def detect_post_outcome_features(df: pd.DataFrame, feature_columns: list[str]) -> list[dict]:
    """Role-based post-outcome detection — see
    data_understanding_service.detect_post_outcome_columns for the full rationale. This
    used to be a name-hint-plus-correlation-vs-an-arbitrary-target heuristic; that
    correlation "evidence" was proven unreliable (verified directly: a genuine leak and
    pure statistical noise can both show under 1% correlation, so no threshold value can
    tell them apart). It's replaced by a target-INDEPENDENT structural signal instead:
    whether this dataset is shaped one-row-per-event (transaction_log/time_series, where a
    post-outcome-hint column describes THIS row's own aftermath) vs. one-row-per-entity
    (customer_level/general, where the same words describe accumulated past history, e.g.
    'complaints_last_6_months', and must NOT be flagged).

    Unlike the old function, this has no per-column correlation signal to distinguish "a
    dominant single-feature leak" from "merely plausible" — since that distinction was
    itself built on the same unreliable correlation evidence, every flagged column is now
    treated uniformly as excluded-by-default-but-overridable (the caller's existing
    "review" tier), never as confirmed/non-overridable leakage.
    """
    if not feature_columns:
        return []
    # Classified against the FULL dataframe (not just feature_columns), since dataset-type
    # detection needs to see the customer_id/timestamp columns that select_training_features
    # already excluded from the feature LIST — those columns are still present in df itself.
    profile = build_dataset_profile(df)
    column_roles = classify_columns(df, profile, use_gemini=False)
    dataset_type = detect_dataset_type(df, column_roles)
    flagged = detect_post_outcome_columns(column_roles, dataset_type["type"])
    return [w for w in flagged if w["column"] in feature_columns]


class FeatureResolution:
    def __init__(self):
        self.features: list[str] = []
        self.excluded_id_like: list[str] = []
        self.excluded_datetime_raw: list[str] = []
        self.derived_date_features: list[str] = []
        self.leakage_warnings: list[dict] = []
        self.post_outcome_excluded: list[dict] = []
        self.post_outcome_included: list[dict] = []
        self.high_correlation_warnings: list[dict] = []


def resolve_training_features(
    df: pd.DataFrame,
    target_column: str,
    feature_columns: list[str] | None,
    include_post_outcome_features: list[str] | None = None,
) -> tuple[pd.DataFrame, FeatureResolution]:
    """The single shared place that decides which columns a model is allowed to see.
    Both Auto Analyze and manual training (ModelingTab) call train_models(), and
    train_models calls exactly this function — there is no second, divergent copy of this
    logic anywhere else.

    Order of decisions: ID-like columns are excluded outright; raw date columns are
    replaced with derived year/month/day-of-week features; single-column and pairwise
    (derived) leakage is excluded unconditionally (never overridable — it's a statistical
    near-certainty); features named like a computed financial outcome (profit, margin, ...)
    with a real correlation to the target are excluded unconditionally too (a derived
    metric, not a legitimate input); post-outcome-named features that are themselves a
    clear statistical outlier among their peers are excluded unconditionally too (a
    single-feature leak); the REMAINING post-outcome-named-and-correlated features are
    excluded BY DEFAULT, but the caller can explicitly opt specific ones back in via
    include_post_outcome_features (the "Retrain including selected" checkbox flow).
    Finally, any RETAINED feature with a high (but not near-duplicate) correlation to the
    target is flagged for review (not excluded — see detect_high_correlation_features).
    """
    resolution = FeatureResolution()
    include_post_outcome_features = set(include_post_outcome_features or [])

    if feature_columns:
        candidate_features = [c for c in feature_columns if c in df.columns and c != target_column]
    else:
        df, selection = select_training_features(df, target_column)
        candidate_features = selection.features
        resolution.excluded_id_like = selection.excluded_id_like
        resolution.excluded_datetime_raw = selection.excluded_datetime_raw
        resolution.derived_date_features = selection.derived_date_features

    if not candidate_features:
        raise DatasetValidationError("No valid feature columns are available for training.")

    leakage_warnings = detect_leakage(df, target_column, candidate_features)
    leakage_warnings += detect_pairwise_leakage(df, target_column, candidate_features)
    leaked_columns = {w["column"] for w in leakage_warnings}
    candidate_features = [c for c in candidate_features if c not in leaked_columns]
    if not candidate_features:
        raise DatasetValidationError(
            "All candidate features were excluded as likely data leakage from the target "
            "(near-perfectly correlated with it, alone or combined). Please select feature "
            "columns manually."
        )

    derived_metric_warnings = detect_derived_metric_features(df, target_column, candidate_features)
    if derived_metric_warnings:
        leakage_warnings += derived_metric_warnings
        derived_metric_names = {w["column"] for w in derived_metric_warnings}
        candidate_features = [c for c in candidate_features if c not in derived_metric_names]
    if not candidate_features:
        raise DatasetValidationError(
            "All candidate features were excluded as likely data leakage from the target "
            "(computed financial outcomes correlated with it). Please select feature columns "
            "manually."
        )

    post_outcome_review = detect_post_outcome_features(df, candidate_features)

    post_outcome_excluded = []
    post_outcome_included = []
    for warning in post_outcome_review:
        if warning["column"] in include_post_outcome_features:
            post_outcome_included.append(warning)
        else:
            post_outcome_excluded.append(warning)
    excluded_default_names = {w["column"] for w in post_outcome_excluded}
    candidate_features = [c for c in candidate_features if c not in excluded_default_names]

    if not candidate_features:
        raise DatasetValidationError(
            "All candidate features were excluded as likely data leakage or post-outcome "
            "information. Please select feature columns manually, or re-include specific "
            "post-outcome features."
        )

    resolution.features = candidate_features
    resolution.leakage_warnings = leakage_warnings
    resolution.post_outcome_excluded = post_outcome_excluded
    resolution.post_outcome_included = post_outcome_included
    resolution.high_correlation_warnings = detect_high_correlation_features(df, target_column, candidate_features)
    return df, resolution


def _build_feature_schema(X: pd.DataFrame, numeric_features: list[str], categorical_features: list[str]) -> list[dict]:
    """Per-feature metadata used to auto-generate the prediction form: type, a sensible
    default (median for numeric, mode for categorical), and the real category options seen
    during training (so dropdowns can't submit a value the model was never fit on)."""
    schema = []
    for col in numeric_features:
        series = pd.to_numeric(X[col], errors="coerce").dropna()
        median = float(series.median()) if len(series) else 0.0
        schema.append(
            {
                "name": col,
                "type": "numeric",
                "default": round(median, 4),
                "min": round(float(series.min()), 4) if len(series) else None,
                "max": round(float(series.max()), 4) if len(series) else None,
            }
        )
    for col in categorical_features:
        series = X[col].dropna().astype(str)
        options = sorted(series.unique().tolist())[:200]
        mode_vals = series.mode()
        default = mode_vals.iloc[0] if len(mode_vals) else (options[0] if options else "")
        schema.append(
            {
                "name": col,
                "type": "categorical",
                "default": default,
                "options": options,
            }
        )
    return schema


def train_models(
    db: Session,
    project_id,
    dataset: Dataset,
    target_column: str,
    feature_columns,
    test_size: float,
    random_seed: int,
    model_keys: list[str] | None,
    cv_folds: int = 5,
    include_post_outcome_features: list[str] | None = None,
) -> list[MLModel]:
    df = load_dataframe(dataset)

    if target_column not in df.columns:
        raise DatasetValidationError(
            "The selected target column could not be found. Please select another column."
        )

    df = df.dropna(subset=[target_column])
    if len(df) < 20:
        raise DatasetValidationError(
            "This dataset does not contain enough valid rows to train a reliable model "
            "(at least 20 rows with a non-missing target are required)."
        )

    df, resolution = resolve_training_features(df, target_column, feature_columns, include_post_outcome_features)
    all_features = resolution.features
    excluded_id_like = resolution.excluded_id_like
    excluded_datetime_raw = resolution.excluded_datetime_raw
    derived_date_features = resolution.derived_date_features
    leakage_warnings = resolution.leakage_warnings
    post_outcome_excluded = resolution.post_outcome_excluded
    post_outcome_included = resolution.post_outcome_included
    high_correlation_warnings = resolution.high_correlation_warnings

    cv_folds = max(2, min(cv_folds or 5, 10))

    X = df[all_features]
    y_raw = df[target_column]

    problem_type = detect_problem_type(y_raw)

    label_map = None
    if problem_type == "classification":
        categories = sorted(y_raw.astype(str).unique().tolist())
        label_map = {c: i for i, c in enumerate(categories)}
        y = y_raw.astype(str).map(label_map)
    else:
        y = pd.to_numeric(y_raw, errors="coerce")
        valid_idx = y.notna()
        X, y = X[valid_idx], y[valid_idx]

    numeric_features = X.select_dtypes(include=[np.number]).columns.tolist()
    categorical_features = [c for c in X.columns if c not in numeric_features]
    feature_schema = _build_feature_schema(X, numeric_features, categorical_features)

    stratify = y if problem_type == "classification" and y.nunique() > 1 else None
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_seed, stratify=stratify
        )
    except ValueError:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_seed
        )

    candidates = CLASSIFICATION_MODELS if problem_type == "classification" else REGRESSION_MODELS
    if model_keys:
        # Explicit selection (Modeling tab checkboxes) is never restricted by dataset size
        # — that gating only applies to the automatic (Auto Analyze) path below.
        selected_keys = [k for k in model_keys if k in candidates]
        if not selected_keys:
            selected_keys = recommend_model_keys(problem_type, len(X_train))
    else:
        selected_keys = recommend_model_keys(problem_type, len(X_train))

    class_weight = _classification_class_weight(y_train) if problem_type == "classification" else None

    comparison_key = "f1" if problem_type == "classification" else "r2"

    # A baseline (majority-class / mean predictor) trained models are compared against —
    # not so a model "passes" some arbitrary bar, but so a weak result is honestly labeled
    # as barely-better-than-guessing rather than presented as a real predictive model.
    if problem_type == "classification":
        baseline_value = y_train.mode().iloc[0] if len(y_train.mode()) else y_train.iloc[0]
        baseline_pred = np.full(len(y_test), baseline_value)
        baseline_metrics = classification_metrics(y_test, baseline_pred, None)
    else:
        baseline_value = float(y_train.mean())
        baseline_pred = np.full(len(y_test), baseline_value)
        baseline_metrics = regression_metrics(y_test, baseline_pred)
    baseline_score = float(baseline_metrics.get(comparison_key, 0) or 0)

    results = []
    saved_models: list[MLModel] = []
    existing_count = (
        db.query(MLModel)
        .filter(MLModel.project_id == project_id, MLModel.dataset_id == dataset.id, MLModel.target_column == target_column)
        .count()
    )

    for key in selected_keys:
        preprocessor = _build_preprocessor(numeric_features, categorical_features)
        estimator = candidates[key](random_seed, class_weight)
        pipeline = Pipeline(steps=[("preprocessor", preprocessor), ("model", estimator)])

        pipeline.fit(X_train, y_train)
        y_pred = pipeline.predict(X_test)

        cv_scores = []
        try:
            scoring = "f1_weighted" if problem_type == "classification" else "r2"
            effective_cv = min(cv_folds, y_train.value_counts().min()) if problem_type == "classification" else cv_folds
            effective_cv = max(2, effective_cv)
            cv_scores = cross_val_score(pipeline, X_train, y_train, cv=effective_cv, scoring=scoring).tolist()
        except Exception:
            cv_scores = []

        if problem_type == "classification":
            y_proba = pipeline.predict_proba(X_test) if hasattr(pipeline, "predict_proba") else None
            metrics = classification_metrics(y_test, y_pred, y_proba)
            reverse_map = {v: k for k, v in (label_map or {}).items()}
            labels_sorted = sorted(set(y_test) | set(y_pred))
            cm = confusion_matrix(y_test, y_pred, labels=labels_sorted).tolist()
            metrics["confusion_matrix"] = {
                "labels": [reverse_map.get(int(l), str(l)) for l in labels_sorted],
                "matrix": cm,
            }
            metrics["class_distribution"] = {
                reverse_map.get(int(k), str(k)): int(v) for k, v in y_test.value_counts().items()
            }
        else:
            metrics = regression_metrics(y_test, y_pred)
            sample_idx = np.random.RandomState(random_seed).choice(
                len(y_test), size=min(30, len(y_test)), replace=False
            )
            y_test_arr = np.asarray(y_test)
            metrics["actual_vs_predicted"] = [
                {"actual": round(float(y_test_arr[i]), 4), "predicted": round(float(y_pred[i]), 4)}
                for i in sample_idx
            ]
            metrics["residuals_sample"] = [
                round(float(y_test_arr[i] - y_pred[i]), 4) for i in sample_idx
            ]

        importance = _feature_importance(pipeline, numeric_features, categorical_features)

        model_score = float(metrics.get(comparison_key, 0) or 0)
        cv_std = float(np.std(cv_scores)) if len(cv_scores) > 1 else 0.0
        improvement = model_score - baseline_score
        # "Clearly beats" is judged against the model's OWN cross-validation spread rather
        # than a fixed number — if the improvement over baseline is smaller than the
        # model's natural fold-to-fold variance, it isn't distinguishable from noise.
        clearly_beats_baseline = improvement > cv_std if cv_std > 0 else improvement > 0
        baseline_comparison = {
            "baseline_score": round(baseline_score, 4),
            "baseline_metric": comparison_key,
            "model_score": round(model_score, 4),
            "improvement": round(improvement, 4),
            "cv_std": round(cv_std, 4),
            "clearly_beats_baseline": bool(clearly_beats_baseline),
        }
        is_weak, weak_reason = evaluate_model_strength(metrics, baseline_comparison)

        model_dir = os.path.join(settings.MODEL_DIR, str(project_id))
        os.makedirs(model_dir, exist_ok=True)
        model_path = os.path.join(model_dir, f"{key}_{uuid.uuid4().hex}.joblib")
        joblib.dump(
            {
                "pipeline": pipeline,
                "label_map": label_map,
                "numeric_features": numeric_features,
                "categorical_features": categorical_features,
                "problem_type": problem_type,
            },
            model_path,
        )

        ml_model = MLModel(
            project_id=project_id,
            dataset_id=dataset.id,
            version=existing_count + len(saved_models) + 1,
            model_type=key,
            problem_type=problem_type,
            target_column=target_column,
            feature_columns_json=all_features,
            preprocessing_json={
                "numeric_features": numeric_features,
                "categorical_features": categorical_features,
                "test_size": test_size,
                "cv_folds": cv_folds,
                "excluded_id_like": excluded_id_like,
                "excluded_datetime_raw": excluded_datetime_raw,
                "derived_date_features": derived_date_features,
                "class_weight_applied": class_weight,
            },
            hyperparameters_json={"random_seed": random_seed},
            metrics_json={
                **metrics,
                "cv_scores": cv_scores,
                "cv_mean": round(float(np.mean(cv_scores)), 4) if cv_scores else None,
                "baseline": baseline_comparison,
                "leakage_warnings": leakage_warnings,
                "post_outcome_excluded": post_outcome_excluded,
                "post_outcome_included": post_outcome_included,
                "high_correlation_warnings": high_correlation_warnings,
                "is_weak": is_weak,
                "weak_reason": weak_reason,
            },
            feature_importance_json=importance,
            feature_schema_json=feature_schema,
            random_seed=random_seed,
            model_path=model_path,
            status="completed",
        )
        db.add(ml_model)
        results.append((ml_model, model_score))
        saved_models.append(ml_model)

    db.flush()

    if results:
        best_model, _ = max(results, key=lambda r: r[1])
        best_model.is_best = True

    comparison_summary = [
        {"model_type": m.model_type, "label": MODEL_LABELS.get(m.model_type, m.model_type), "metrics": m.metrics_json}
        for m in saved_models
    ]
    for m in saved_models:
        m.comparison_json = {"models": comparison_summary}

    db.commit()
    for m in saved_models:
        db.refresh(m)

    return saved_models


CLUSTERING_MIN_K = 2
CLUSTERING_MAX_K = 8
CLUSTERING_SKEW_THRESHOLD = 1.0  # standard statistical convention for "significantly skewed"
CLUSTERING_STABILITY_BOOTSTRAP = 10


def _clustering_feature_columns(
    numeric_cols: list[str], column_roles: dict[str, dict] | None
) -> tuple[list[str], list[str]]:
    """Splits numeric, non-ID-like columns into (behavioral, descriptive) — standard
    segmentation methodology clusters on BEHAVIORAL/USAGE signal only; demographics (age),
    plan/category attributes, and any date-derived feature describe the resulting segments
    afterward, they never form them (a customer's age doesn't change segment membership,
    it explains one once membership is already decided by behavior). Only 'age' needs an
    explicit exclusion here — other demographics (city, gender, plan type) are already
    categorical, so they're never in numeric_cols to begin with.

    A derived date part (`{timestamp_col}_year`/`_month`/`_dayofweek`, ml_service's own
    naming convention from select_training_features) is excluded defensively in case a
    future caller's cleaned frame ever contains one — but ONLY when the base column
    (with the suffix stripped) is ITSELF classified as a timestamp; a naive suffix check
    alone would wrongly catch a genuine usage metric like 'call_minutes_month' or
    'data_usage_gb_month', which merely happen to end in '_month' without being derived
    from any date column at all (verified: this exact false positive was found and fixed
    while building this function, against real telecom data)."""
    behavioral, descriptive = [], []
    for col in numeric_cols:
        tokens = tokenize_column_name(col)
        role = (column_roles or {}).get(col, {}).get("role")
        is_derived_date_part = False
        for suffix in ("_year", "_month", "_dayofweek"):
            if col.endswith(suffix):
                base_col = col[: -len(suffix)]
                if (column_roles or {}).get(base_col, {}).get("role") == ROLE_TIMESTAMP:
                    is_derived_date_part = True
                break
        if AGE_NAME_TOKEN in tokens or role in (ROLE_IDENTIFIER, ROLE_CUSTOMER_ID) or is_derived_date_part:
            descriptive.append(col)
        else:
            behavioral.append(col)
    return behavioral, descriptive


def _log_transform_skewed(df: pd.DataFrame, threshold: float = CLUSTERING_SKEW_THRESHOLD) -> tuple[pd.DataFrame, list[str]]:
    """Log1p-transforms any column whose distribution is significantly right-skewed (a
    standard convention: sample skewness > 1) — usage/count metrics (minutes, GB,
    transactions) are almost always right-skewed (most customers use a little, a few use
    a lot), and clustering on the raw scale lets those few extreme values dominate the
    Euclidean distance the whole clustering is based on, over the bulk of normal usage.
    Skipped for any column with a non-positive value (log1p needs x >= -1; a column that's
    already mixed-sign isn't a plain right-skewed count/usage metric anyway)."""
    df = df.copy()
    transformed = []
    for col in df.columns:
        series = df[col]
        if series.min() < 0:
            continue
        s = _scipy_skew(series.dropna())
        if pd.notna(s) and s > threshold:
            df[col] = np.log1p(series)
            transformed.append(col)
    return df, transformed


def _gmm_bic(X: np.ndarray, k: int, seed: int) -> float:
    """Bayesian Information Criterion for a k-component Gaussian Mixture fit to the same
    scaled data KMeans clusters — a second, independent model-selection signal alongside
    silhouette: BIC penalizes model complexity directly (more components must earn their
    keep via a real improvement in fit, not just a smaller silhouette-optimal partition),
    so combining the two catches a k that only looks good by one measure."""
    gmm = GaussianMixture(n_components=k, random_state=seed, n_init=1)
    gmm.fit(X)
    return float(gmm.bic(X))


def _bootstrap_stability(X: np.ndarray, k: int, seed: int, n_bootstrap: int = CLUSTERING_STABILITY_BOOTSTRAP) -> float:
    """How reproducible a k-cluster partition is under resampling — fits a REFERENCE
    clustering on the full data, then repeatedly refits on a bootstrap resample and
    compares (via Adjusted Rand Index, chance-corrected agreement between two labelings)
    against the reference restricted to the same resampled rows. A k that only fits well
    because it's carving up sampling noise will disagree with itself across resamples; a
    genuine segment structure reproduces. Averaged over n_bootstrap resamples."""
    base_labels = KMeans(n_clusters=k, random_state=seed, n_init=10).fit_predict(X)
    rng = np.random.RandomState(seed)
    n = len(X)
    scores = []
    for i in range(n_bootstrap):
        idx = rng.choice(n, size=n, replace=True)
        boot_labels = KMeans(n_clusters=k, random_state=seed + i + 1, n_init=5).fit_predict(X[idx])
        scores.append(adjusted_rand_score(base_labels[idx], boot_labels))
    return float(np.mean(scores))


def _select_k_by_combined_score(candidates: list[dict]) -> dict:
    """Combines silhouette (higher better), GMM BIC (LOWER better — inverted here so
    higher is always better across all three), and bootstrap stability (higher better)
    into one score by min-max normalizing each across the candidate k's and averaging —
    deliberately NOT silhouette alone, since silhouette alone is exactly what picked k=5
    for a fraud-distorted telecom dataset before (see docs/PROGRESS.md Finding 12/17).
    Never looks at any answer key — every input here comes from the data and the
    candidate clusterings themselves."""
    sils = [c["silhouette"] for c in candidates]
    bics = [c["bic"] for c in candidates]
    sil_range = (max(sils) - min(sils)) or 1.0
    bic_range = (max(bics) - min(bics)) or 1.0
    for c in candidates:
        norm_sil = (c["silhouette"] - min(sils)) / sil_range
        norm_bic = (max(bics) - c["bic"]) / bic_range
        c["combined_score"] = round((norm_sil + norm_bic + c["stability"]) / 3, 4)
    return max(candidates, key=lambda c: c["combined_score"])


def _cluster_descriptive_summary(
    df: pd.DataFrame, cluster_labels: pd.Series, descriptive_numeric: list[str], column_roles: dict[str, dict] | None
) -> dict:
    """Profiles a cluster with the columns deliberately excluded from FORMING it —
    demographics (age) and categorical attributes (city, gender, plan type) — the
    "describe segments afterward" half of standard segmentation methodology. Never
    influences cluster membership, computed purely for reporting."""
    summary: dict = {}
    for col in descriptive_numeric:
        if col in df.columns:
            values = pd.to_numeric(df[col], errors="coerce")
            if values.notna().any():
                summary[col] = round(float(values.mean()), 2)
    category_cols = [
        n for n, r in (column_roles or {}).items() if r.get("role") == ROLE_CATEGORY and n in df.columns
    ]
    for col in category_cols:
        counts = df[col].value_counts(dropna=True).head(3)
        total = counts.sum()
        if total > 0:
            summary[col] = {
                str(val): round(float(count) / float(total) * 100, 1) for val, count in counts.items()
            }
    return summary


def run_clustering_analysis(
    dataset: Dataset, df: pd.DataFrame, column_roles: dict[str, dict] | None = None, max_k: int = CLUSTERING_MAX_K
) -> dict | None:
    """Unsupervised fallback used by Auto Analyze when no target column can be confidently
    detected — spec: 'If no target exists, allow analysis without supervised ML' instead of
    forcing every dataset into classification/regression. Returns None when there isn't
    enough usable numeric structure to cluster meaningfully (fewer than 2 behavioral
    columns or fewer than 20 complete rows) — the caller falls back to an EDA-only general
    analysis in that case, never a fake result.

    Standard segmentation methodology, applied generally (not just for one dataset):
      1. Cluster on BEHAVIORAL/USAGE columns only — demographics (age), category columns,
         and derived date parts are excluded from FORMING clusters (_clustering_feature_columns)
         and instead used to DESCRIBE the resulting segments (_cluster_descriptive_summary).
      2. Right-skewed usage columns are log1p-transformed before scaling (_log_transform_skewed)
         — untransformed, a few extreme values would dominate the distance metric.
      3. k is chosen from silhouette, GMM BIC, and bootstrap resample stability TOGETHER
         (_select_k_by_combined_score), never silhouette alone — a single statistical
         measure can be misled by a distorted or noisy feature (this is exactly what
         previously picked k=5 for telecom before implausible values were corrected;
         see docs/PROGRESS.md Findings 12/16/17).
      4. `column_roles` is optional (computed internally via classify_columns if omitted)
         so this remains callable standalone, e.g. from tests, without every caller having
         to run Data Understanding first.
    """
    profile = dataset.profile_json or {}
    numeric_cols = [
        c["name"] for c in profile.get("columns", []) if c.get("is_numeric") and not c.get("is_id_like")
    ]
    if len(numeric_cols) < 2:
        return None

    if column_roles is None:
        column_roles = classify_columns(df, profile, use_gemini=False)

    behavioral_cols, descriptive_numeric_cols = _clustering_feature_columns(numeric_cols, column_roles)
    if len(behavioral_cols) < 2:
        # Not enough BEHAVIORAL signal even if the raw numeric column count looked
        # sufficient (e.g. a dataset that's mostly demographics) — fall back honestly
        # rather than clustering on demographics alone, which standard methodology
        # treats as a describe-afterward signal, not a clustering input.
        behavioral_cols, descriptive_numeric_cols = numeric_cols, []

    usable = df[behavioral_cols].apply(pd.to_numeric, errors="coerce").dropna()
    if len(usable) < 20:
        return None

    usable_transformed, log_transformed_cols = _log_transform_skewed(usable)

    preprocessor = Pipeline(steps=[("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())])
    X_scaled = preprocessor.fit_transform(usable_transformed)

    upper_k = min(max_k, len(usable) // 10, len(usable) - 1)
    seed = 42
    candidates: list[dict] = []
    for k in range(CLUSTERING_MIN_K, max(CLUSTERING_MIN_K + 1, upper_k + 1)):
        if k >= len(usable):
            break
        labels = KMeans(n_clusters=k, random_state=seed, n_init=10).fit_predict(X_scaled)
        if len(set(labels)) < 2:
            continue
        candidates.append(
            {
                "k": k,
                "silhouette": round(float(silhouette_score(X_scaled, labels)), 4),
                "bic": round(_gmm_bic(X_scaled, k, seed), 2),
                "stability": round(_bootstrap_stability(X_scaled, k, seed), 4),
                "labels": labels,
            }
        )

    if not candidates:
        return None

    best = _select_k_by_combined_score(candidates)
    best_k, best_labels = best["k"], best["labels"]

    labeled = usable.copy()
    labeled["_cluster"] = best_labels
    id_col = next(
        (n for n, r in (column_roles or {}).items() if r.get("role") in (ROLE_CUSTOMER_ID, ROLE_IDENTIFIER)), None
    )
    cluster_profiles = []
    for cluster_id in sorted(labeled["_cluster"].unique()):
        subset = labeled[labeled["_cluster"] == cluster_id]
        cluster_profiles.append(
            {
                "cluster": int(cluster_id),
                "size": int(len(subset)),
                "pct": round(len(subset) / len(labeled) * 100, 1),
                "feature_means": {col: round(float(subset[col].mean()), 4) for col in behavioral_cols},
                "descriptive_summary": _cluster_descriptive_summary(
                    df.loc[subset.index], subset["_cluster"], descriptive_numeric_cols, column_roles
                ),
            }
        )

    predictions = [
        {"id": (df.loc[idx, id_col] if id_col and id_col in df.columns else idx), "cluster": int(cluster)}
        for idx, cluster in zip(usable.index, best_labels)
    ]

    return {
        "method": "kmeans",
        "k": best_k,
        "silhouette_score": best["silhouette"],
        "features_used": behavioral_cols,
        "descriptive_features": descriptive_numeric_cols,
        "log_transformed_features": log_transformed_cols,
        "rows_clustered": int(len(usable)),
        "clusters": cluster_profiles,
        "predictions": predictions,
        "k_selection": [
            {"k": c["k"], "silhouette": c["silhouette"], "bic": c["bic"], "stability": c["stability"], "combined_score": c["combined_score"]}
            for c in candidates
        ],
    }
