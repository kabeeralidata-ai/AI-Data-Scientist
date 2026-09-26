"""End-to-end proof that Auto Analyze on the real Karachi food-delivery dataset produces
a correct, non-leaky, properly-cleaned model — the exact scenario reported as broken:
distance_km being treated as text, tip_pkr/customer_rating/delivery_time_min still
driving training, and a suspiciously perfect model score.

promised_time_min is deliberately NOT excluded (see
ml_service._outcome_side_of_pair): it correlates with the target only alongside
delivery_time_min (late_delivery = delivery_time_min > promised_time_min), but unlike
delivery_time_min it's a committed SLA value known BEFORE the outcome, identifiable from
its own much lower cardinality (a handful of standard tiers vs. delivery_time_min's
near-continuous realized values) — a real, legitimate, available-in-advance feature."""

import os

from app.core.database import SessionLocal
from app.models.auto_analyze_job import AutoAnalyzeJob
from app.models.dataset import Dataset
from app.services import auto_analyze_service

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "karachi_food_delivery_dataset.csv")


def test_auto_analyze_end_to_end_on_the_real_karachi_dataset(auth_client):
    project_resp = auth_client.post("/api/projects", json={"name": "Karachi E2E Project"})
    project_id = project_resp.json()["id"]

    with open(DATA_PATH, "rb") as f:
        upload_resp = auth_client.post(
            f"/api/projects/{project_id}/datasets/upload",
            files={"file": ("karachi_food_delivery_dataset.csv", f, "text/csv")},
        )
    assert upload_resp.status_code == 201
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start_resp.status_code == 201
    job_id = start_resp.json()["id"]

    paused = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert paused["status"] == "awaiting_target_confirmation"

    profile_step = next(s for s in paused["steps_json"] if s["key"] == "profile")
    assert "2520 rows" in profile_step["detail"]
    assert "18 columns" in profile_step["detail"]

    cleaning_step = next(s for s in paused["steps_json"] if s["key"] == "cleaning")
    assert "2520 rows" in cleaning_step["detail"]
    assert "2500 rows after cleaning" in cleaning_step["detail"]

    confirm_resp = auth_client.post(
        f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "late_delivery"}
    )
    assert confirm_resp.status_code == 200

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "completed", job.get("error_message")

    # distance_km must now be numeric after cleaning (unit-stripped from "4.2 km" style
    # values), and EDA must count it among the numeric columns.
    dataset_detail = auth_client.get(f"/api/datasets/{dataset_id}").json()
    distance_col = next(c for c in dataset_detail["columns"] if c["name"] == "distance_km")
    assert distance_col["is_numeric"] is True

    eda_resp = auth_client.post(f"/api/analysis/{dataset_id}/eda")
    assert eda_resp.status_code == 200
    assert eda_resp.json()["summary"]["numeric_columns"] == 10

    training_step = next(s for s in job["steps_json"] if s["key"] == "training")
    assert training_step["status"] == "completed"

    best_model_id = job["result_json"]["best_model_id"]
    model_detail = auth_client.get(f"/api/models/{best_model_id}").json()
    features = model_detail["feature_columns_json"]

    excluded = {"order_id", "delivery_time_min", "tip_pkr", "customer_rating", "late_delivery"}
    for col in excluded:
        assert col not in features, f"'{col}' must not be used as a training feature"

    included = {
        "distance_km", "traffic_level", "weather", "vehicle_type",
        "prep_time_min", "rider_rating", "rider_experience_months", "promised_time_min",
    }
    for col in included:
        assert col in features, f"'{col}' should be a legitimate training feature"

    best_score = job["result_json"]["best_model_score"]
    assert best_score < 0.95, f"best model score {best_score} is suspiciously perfect — likely leakage"

    baseline = job["result_json"]["baseline"]
    assert best_score > baseline["baseline_score"]
    assert baseline["clearly_beats_baseline"] is True

    print(f"\nKarachi E2E: best_model={job['result_json']['best_model_type']} score={best_score}")


def test_shared_feature_resolution_used_by_manual_training_matches_auto_analyze(auth_client):
    """Confirms there is exactly one feature-selection code path: training the same
    dataset/target manually via /api/models/train (no Auto Analyze involved) excludes the
    exact same columns as the Auto Analyze run above."""
    project_resp = auth_client.post("/api/projects", json={"name": "Karachi Manual Project"})
    project_id = project_resp.json()["id"]

    with open(DATA_PATH, "rb") as f:
        upload_resp = auth_client.post(
            f"/api/projects/{project_id}/datasets/upload",
            files={"file": ("karachi_food_delivery_dataset.csv", f, "text/csv")},
        )
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "late_delivery", "models": ["gradient_boosting"]},
    )
    assert train_resp.status_code == 201
    features = train_resp.json()[0]["feature_columns_json"]

    for col in ("order_id", "delivery_time_min", "tip_pkr", "customer_rating"):
        assert col not in features
    for col in ("distance_km", "traffic_level", "weather", "vehicle_type", "promised_time_min"):
        assert col in features
