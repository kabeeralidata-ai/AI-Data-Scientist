"""Direct unit tests for resolve_training_features — the single shared function both
Auto Analyze and manual training (ModelingTab) call for ALL feature-selection decisions
(ID exclusion, leakage exclusion, post-outcome exclusion, user include-back choices)."""

import random

import pandas as pd
import pytest

from app.services.ml_service import resolve_training_features
from app.utils.validators import DatasetValidationError


def test_excludes_id_like_columns_by_default():
    df = pd.DataFrame({
        "customer_id": [f"CUST-{i}" for i in range(50)],
        "age": [20 + i % 40 for i in range(50)],
        "churn": [i % 2 for i in range(50)],
    })
    _, resolution = resolve_training_features(df, "churn", None)
    assert "customer_id" not in resolution.features
    assert "age" in resolution.features
    assert "customer_id" in resolution.excluded_id_like


def test_excludes_single_column_leakage_by_default():
    df = pd.DataFrame({
        "sales_amount": list(range(1, 101)),
        "profit": [x * 0.2 for x in range(1, 101)],  # near-perfectly correlated
        "quantity": [i % 7 for i in range(1, 101)],
    })
    _, resolution = resolve_training_features(df, "sales_amount", None)
    assert "profit" not in resolution.features
    assert "quantity" in resolution.features
    leaked = {w["column"] for w in resolution.leakage_warnings}
    assert "profit" in leaked


def test_post_outcome_named_feature_excluded_by_default_and_overridable():
    random.seed(3)
    n = 200
    rows = []
    for _ in range(n):
        order_value = random.uniform(100, 1000)
        late = random.random() < 0.4
        tip_pkr = round(random.uniform(10, 40) if late else random.uniform(30, 70), 2)
        rows.append({"order_value": order_value, "tip_pkr": tip_pkr, "late": "Yes" if late else "No"})
    df = pd.DataFrame(rows)

    _, default_resolution = resolve_training_features(df, "late", None)
    assert "tip_pkr" not in default_resolution.features
    assert "order_value" in default_resolution.features
    excluded = {w["column"] for w in default_resolution.post_outcome_excluded}
    assert "tip_pkr" in excluded

    _, override_resolution = resolve_training_features(df, "late", None, include_post_outcome_features=["tip_pkr"])
    assert "tip_pkr" in override_resolution.features
    included = {w["column"] for w in override_resolution.post_outcome_included}
    assert "tip_pkr" in included
    assert override_resolution.post_outcome_excluded == []


def test_raises_when_every_candidate_feature_is_excluded():
    df = pd.DataFrame({
        "record_id": [f"R-{i}" for i in range(30)],
        "target": [i % 2 for i in range(30)],
    })
    with pytest.raises(DatasetValidationError):
        resolve_training_features(df, "target", None)


def test_manual_feature_columns_still_run_through_leakage_detection():
    """Even when the caller passes an explicit feature list (manual mode), leakage
    detection must still run on it — the shared function is the single place this logic
    lives, regardless of how the candidate list was produced."""
    df = pd.DataFrame({
        "sales_amount": list(range(1, 101)),
        "profit": [x * 0.2 for x in range(1, 101)],
        "quantity": [i % 7 for i in range(1, 101)],
    })
    _, resolution = resolve_training_features(df, "sales_amount", ["profit", "quantity"])
    assert "profit" not in resolution.features
    assert "quantity" in resolution.features


def test_excludes_noisy_derived_metric_below_the_near_duplicate_threshold():
    """Regression test for the real bug: a REALISTIC 'profit' column (computed from the
    target with genuine noise, like an actual retail dataset — NOT the artificially exact
    x * 0.2 multiple used above) only correlates ~0.85-0.90 with the target, well under
    the near-duplicate leakage threshold (0.95), so detect_leakage alone misses it. It
    must still be caught and excluded via the name ('profit' = a value computed FROM
    other monetary fields) + moderate-correlation combined signal."""
    random.seed(42)
    n = 500
    unit_price = [random.uniform(5, 200) for _ in range(n)]
    quantity = [random.randint(1, 10) for _ in range(n)]
    sales_amount = [round(p * q, 2) for p, q in zip(unit_price, quantity)]
    # profit as a noisy 10-45% margin of sales_amount plus independent additive noise —
    # realistic (matches the real dataset's ~0.89 correlation), not an exact multiple.
    profit = [round(max(0.5, s * random.uniform(0.10, 0.45) + random.uniform(-10, 10)), 2) for s in sales_amount]
    df = pd.DataFrame(
        {
            "sales_amount": sales_amount,
            "profit": profit,
            "unit_price": unit_price,
            "quantity": quantity,
        }
    )
    raw_corr = df["profit"].corr(df["sales_amount"])
    assert 0.6 <= raw_corr < 0.95, f"fixture must reproduce the noisy, sub-0.95 correlation band (got {raw_corr})"

    _, resolution = resolve_training_features(df, "sales_amount", None)

    assert "profit" not in resolution.features
    assert "unit_price" in resolution.features
    assert "quantity" in resolution.features

    leaked = {w["column"] for w in resolution.leakage_warnings}
    assert "profit" in leaked
    profit_warning = next(w for w in resolution.leakage_warnings if w["column"] == "profit")
    assert "computed" in profit_warning["reason"].lower() or "derive" in profit_warning["reason"].lower()


def test_legitimate_strong_predictor_is_flagged_not_removed():
    """A column that legitimately, strongly predicts the target (unit_price predicting
    sales_amount, which is literally price * quantity) must NOT be auto-excluded just for
    correlating highly with the target — unlike 'profit', it has no derived-metric name
    hint, so it's only surfaced via high_correlation_warnings for human review, and stays
    in the trained feature set."""
    random.seed(7)
    n = 500
    unit_price = [random.uniform(5, 300) for _ in range(n)]
    quantity = [random.randint(1, 3) for _ in range(n)]
    sales_amount = [round(p * q, 2) for p, q in zip(unit_price, quantity)]
    df = pd.DataFrame({"sales_amount": sales_amount, "unit_price": unit_price, "quantity": quantity})

    _, resolution = resolve_training_features(df, "sales_amount", None)

    assert "unit_price" in resolution.features
    assert resolution.leakage_warnings == []
    flagged = {w["column"] for w in resolution.high_correlation_warnings}
    assert "unit_price" in flagged


def test_derived_metric_name_with_low_correlation_is_not_removed():
    """Safeguard: a column merely NAMED like a derived metric (e.g. 'margin') must not be
    excluded just because of its name if it shows no real relationship with THIS target —
    evidence-based exclusion, not a name-only blocklist."""
    random.seed(11)
    n = 300
    df = pd.DataFrame(
        {
            "target": [random.uniform(0, 100) for _ in range(n)],
            # unrelated to target — pure noise, despite the suspicious name.
            "margin": [random.uniform(0, 1) for _ in range(n)],
            "feature_a": [random.uniform(0, 50) for _ in range(n)],
        }
    )
    _, resolution = resolve_training_features(df, "target", None)
    assert "margin" in resolution.features
    leaked = {w["column"] for w in resolution.leakage_warnings}
    assert "margin" not in leaked
