"""Tests for weak-model detection (evaluate_model_strength) and its effect on training
results — the "ROC-AUC ~0.5 got a green checkmark" bug."""

import random

from app.services.ml_service import evaluate_model_strength


def test_weak_when_baseline_not_clearly_beaten():
    metrics = {"roc_auc": 0.9}  # a high AUC alone shouldn't matter if baseline isn't beaten
    baseline = {"clearly_beats_baseline": False, "baseline_metric": "f1", "baseline_score": 0.6, "model_score": 0.61}
    is_weak, reason = evaluate_model_strength(metrics, baseline)
    assert is_weak is True
    assert "baseline" in reason.lower()


def test_weak_when_roc_auc_close_to_half():
    metrics = {"roc_auc": 0.51}
    baseline = {"clearly_beats_baseline": True}
    is_weak, reason = evaluate_model_strength(metrics, baseline)
    assert is_weak is True
    assert "random guessing" in reason.lower()


def test_weak_when_roc_auc_close_to_half_from_below():
    metrics = {"roc_auc": 0.47}
    baseline = {"clearly_beats_baseline": True}
    is_weak, reason = evaluate_model_strength(metrics, baseline)
    assert is_weak is True


def test_not_weak_when_roc_auc_clearly_above_half_and_baseline_beaten():
    metrics = {"roc_auc": 0.85}
    baseline = {"clearly_beats_baseline": True}
    is_weak, reason = evaluate_model_strength(metrics, baseline)
    assert is_weak is False
    assert reason is None


def test_not_weak_for_regression_with_no_roc_auc_when_baseline_beaten():
    metrics = {"r2": 0.8}  # no roc_auc key at all (regression)
    baseline = {"clearly_beats_baseline": True}
    is_weak, reason = evaluate_model_strength(metrics, baseline)
    assert is_weak is False


def _grouping_column_csv(n=300):
    """A 5-city grouping column with no real relationship to any feature — the exact
    'store_city' bug: baseline-vs-f1 comparison alone can look fine on an imbalanced
    multi-class target even though the model has zero real discriminative power."""
    random.seed(2)
    cities = ["Karachi", "Lahore", "Islamabad", "Faisalabad", "Multan"]
    rows = ["unit_price,quantity,store_city"]
    for _ in range(n):
        price = round(random.uniform(5, 200), 2)
        qty = random.randint(1, 10)
        city = random.choice(cities)  # independent of price/quantity
        rows.append(f"{price},{qty},{city}")
    return "\n".join(rows)


def test_training_flags_a_garbage_grouping_target_as_weak(auth_client):
    import io

    project_resp = auth_client.post("/api/projects", json={"name": "Weak Model Project"})
    project_id = project_resp.json()["id"]

    content = _grouping_column_csv()
    upload_resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "store_city", "models": ["random_forest"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]

    assert model["metrics_json"]["is_weak"] is True
    assert model["metrics_json"]["weak_reason"]
