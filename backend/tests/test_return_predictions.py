"""Covers the Predictions tab's per-customer return-prediction view and CSV/Excel
download for transaction-log projects (Phase 0 Finding 21's documented remaining gap:
return-prediction never trains an MLModel row, so it never showed up in the
model-based Predictions tab at all). See app/api/predictions.py's return-predictions
endpoints and auto_analyze_service.get_latest_return_predictions."""

import io
import uuid

import pandas as pd
import pytest


def _create_project(client, name="Return Predictions Project"):
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _upload_small_dataset(client, project_id):
    content = "age,income,churn\n25,50000,0\n40,60000,1\n35,55000,0\n" * 5
    resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_completed_transaction_log_job(db_session, project_id, dataset_id, predictions):
    from app.models.auto_analyze_job import AutoAnalyzeJob, AutoAnalyzeJobStatus

    job = AutoAnalyzeJob(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        dataset_id=uuid.UUID(dataset_id),
        status=AutoAnalyzeJobStatus.COMPLETED,
        steps_json=None,
        result_json={
            "analysis_type": "transaction_log",
            "best_model_id": None,
            "transaction_analysis": {
                "return_prediction": {
                    "window_days": 30,
                    "total_customers": len(predictions),
                    "predicted_will_return": sum(1 for p in predictions if p["predicted_return"]),
                    "predicted_will_not_return": sum(1 for p in predictions if not p["predicted_return"]),
                    "predicted_uncertain": 0,
                    "risk_bands": {"Likely to return": ">= 60%"},
                    "risk_group_counts": {"Likely to return": len(predictions)},
                    "holdout_metrics": {"accuracy": 0.8},
                    "predictions": predictions,
                }
            },
        },
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture
def sample_predictions():
    return [
        {"customer_id": "C1", "predicted_return": True, "return_probability": 0.91, "risk_group": "Likely to return"},
        {"customer_id": "C2", "predicted_return": False, "return_probability": 0.12, "risk_group": "At risk of not returning"},
        {"customer_id": "C3", "predicted_return": True, "return_probability": 0.55, "risk_group": "Uncertain"},
    ]


def test_returns_unavailable_when_no_transaction_log_analysis_exists(auth_client):
    project_id = _create_project(auth_client)
    resp = auth_client.get(f"/api/predictions/return-predictions/{project_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["customers"] == []


def test_returns_per_customer_predictions_for_a_transaction_log_project(auth_client, db_session, sample_predictions):
    project_id = _create_project(auth_client)
    dataset_id = _upload_small_dataset(auth_client, project_id)
    _make_completed_transaction_log_job(db_session, project_id, dataset_id, sample_predictions)

    resp = auth_client.get(f"/api/predictions/return-predictions/{project_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["total_customers"] == 3
    assert body["window_days"] == 30
    assert len(body["customers"]) == 3
    assert {c["customer_id"] for c in body["customers"]} == {"C1", "C2", "C3"}
    assert body["risk_group_counts"] == {"Likely to return": 3}


def test_download_csv_contains_all_customers(auth_client, db_session, sample_predictions):
    project_id = _create_project(auth_client)
    dataset_id = _upload_small_dataset(auth_client, project_id)
    _make_completed_transaction_log_job(db_session, project_id, dataset_id, sample_predictions)

    resp = auth_client.get(f"/api/predictions/return-predictions/{project_id}/download", params={"format": "csv"})
    assert resp.status_code == 200
    assert "csv" in resp.headers["content-type"]
    df = pd.read_csv(io.BytesIO(resp.content))
    assert len(df) == 3
    assert set(df["customer_id"]) == {"C1", "C2", "C3"}


def test_download_excel_contains_all_customers(auth_client, db_session, sample_predictions):
    project_id = _create_project(auth_client)
    dataset_id = _upload_small_dataset(auth_client, project_id)
    _make_completed_transaction_log_job(db_session, project_id, dataset_id, sample_predictions)

    resp = auth_client.get(f"/api/predictions/return-predictions/{project_id}/download", params={"format": "xlsx"})
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    df = pd.read_excel(io.BytesIO(resp.content))
    assert len(df) == 3
    assert set(df["customer_id"]) == {"C1", "C2", "C3"}


def test_download_rejects_an_unknown_format(auth_client, db_session, sample_predictions):
    project_id = _create_project(auth_client)
    dataset_id = _upload_small_dataset(auth_client, project_id)
    _make_completed_transaction_log_job(db_session, project_id, dataset_id, sample_predictions)

    resp = auth_client.get(f"/api/predictions/return-predictions/{project_id}/download", params={"format": "pdf"})
    assert resp.status_code == 422


def test_download_404s_when_no_analysis_exists(auth_client):
    project_id = _create_project(auth_client)
    resp = auth_client.get(f"/api/predictions/return-predictions/{project_id}/download")
    assert resp.status_code == 404


def test_returns_404_for_a_project_owned_by_another_user(client, auth_client, db_session, sample_predictions):
    project_id = _create_project(auth_client)
    dataset_id = _upload_small_dataset(auth_client, project_id)
    _make_completed_transaction_log_job(db_session, project_id, dataset_id, sample_predictions)

    client.post("/api/auth/register", json={"name": "Other User", "email": "other@example.com", "password": "password123"})
    login = client.post("/api/auth/login", json={"email": "other@example.com", "password": "password123"})
    client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})

    resp = client.get(f"/api/predictions/return-predictions/{project_id}")
    assert resp.status_code == 404
