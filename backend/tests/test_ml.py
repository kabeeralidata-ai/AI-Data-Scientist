import io
import random


def upload_dataset(client, project_id, content):
    return client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )


def _make_classification_csv(n=120):
    random.seed(42)
    rows = ["age,monthly_spend,purchase_frequency,churn"]
    for _ in range(n):
        age = random.randint(18, 70)
        spend = round(random.uniform(10, 500), 2)
        freq = random.randint(0, 20)
        churn = 1 if spend < 150 and freq < 5 else 0
        rows.append(f"{age},{spend},{freq},{churn}")
    return "\n".join(rows)


def test_train_classification_models_and_compare(auth_client):
    project_resp = auth_client.post("/api/projects", json={"name": "ML Project"})
    project_id = project_resp.json()["id"]

    upload_resp = upload_dataset(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post(
        "/api/models/train",
        json={
            "dataset_id": dataset_id,
            "target_column": "churn",
            "test_size": 0.2,
            "random_seed": 42,
            "models": ["logistic_regression", "random_forest"],
        },
    )
    assert resp.status_code == 201
    models = resp.json()
    assert len(models) == 2
    assert any(m["is_best"] for m in models)
    for m in models:
        assert m["problem_type"] == "classification"
        assert "accuracy" in m["metrics_json"]
        assert m["feature_importance_json"]


def test_predict_returns_class_and_probabilities(auth_client):
    project_resp = auth_client.post("/api/projects", json={"name": "ML Predict Project"})
    project_id = project_resp.json()["id"]

    upload_resp = upload_dataset(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={
            "dataset_id": dataset_id,
            "target_column": "churn",
            "models": ["random_forest"],
        },
    )
    model_id = train_resp.json()[0]["id"]

    pred_resp = auth_client.post(
        "/api/predictions",
        json={
            "model_id": model_id,
            "features": {"age": 30, "monthly_spend": 450, "purchase_frequency": 8},
        },
    )
    assert pred_resp.status_code == 201
    body = pred_resp.json()
    assert "predicted_class" in body["output_json"]


def test_train_rejects_unknown_target_column(auth_client):
    project_resp = auth_client.post("/api/projects", json={"name": "Bad Target"})
    project_id = project_resp.json()["id"]
    upload_resp = upload_dataset(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "not_a_column"},
    )
    assert resp.status_code == 422
