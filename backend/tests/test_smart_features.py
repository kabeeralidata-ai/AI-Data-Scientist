import io
import random


def create_project(client, name="Smart Features Project"):
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _churn_csv_with_id(n=80):
    random.seed(11)
    rows = ["customer_id,age,monthly_spend,purchase_frequency,churn"]
    for i in range(n):
        age = random.randint(18, 70)
        spend = round(random.uniform(10, 500), 2)
        freq = random.randint(0, 20)
        churn = 1 if spend < 150 and freq < 5 else 0
        rows.append(f"CUST-{1000+i},{age},{spend},{freq},{churn}")
    return "\n".join(rows)


def upload_csv(client, project_id, content, filename="data.csv"):
    return client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": (filename, io.BytesIO(content.encode()), "text/csv")},
    )


def test_continuous_numeric_columns_are_not_flagged_as_id_like(auth_client):
    """Regression test: a near-100%-unique NUMERIC column (a price, a precise score...)
    is normal, valuable signal for ML — it must not be excluded from training just
    because every row happens to have a distinct value. Only text/categorical columns
    use the high-cardinality heuristic; numeric ID columns still get caught by name."""
    project_id = create_project(auth_client)
    rows = ["customer_id,monthly_charge,plan,churn"]
    for i in range(50):
        charge = round(15.5 + i * 1.37, 2)  # distinct float per row, like a real price
        plan = "Basic" if i % 2 == 0 else "Premium"
        # Deliberately NOT a function of charge — this test is about is_id_like
        # exclusion, not leakage detection; a target that's a deterministic threshold
        # of the exact feature under test would (correctly) get caught as a real
        # single-column leak by detect_leakage, which isn't what's being tested here.
        churn = 1 if i % 3 == 0 else 0
        rows.append(f"CUST-{i},{charge},{plan},{churn}")
    content = "\n".join(rows)

    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    detail = auth_client.get(f"/api/datasets/{dataset_id}")
    columns = {c["name"]: c for c in detail.json()["columns"]}
    assert columns["customer_id"]["is_id_like"] is True
    assert columns["monthly_charge"]["is_id_like"] is False

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    assert "monthly_charge" in train_resp.json()[0]["feature_columns_json"]


def test_training_without_feature_columns_excludes_date_columns(auth_client):
    """Regression test: a raw CSV read never parses date strings into real datetime64
    dtype, so date-column exclusion must use the same semantic-type detection as the
    dataset profile (name hint + parse attempt) — not a raw pandas dtype check, which
    would silently let a 'signup_date' column into training as a huge one-hot categorical
    and then require it as a prediction input."""
    project_id = create_project(auth_client)
    rows = ["customer_id,age,signup_date,churn"]
    for i in range(50):
        month = (i % 12) + 1
        day = (i % 27) + 1
        churn = 1 if i % 3 == 0 else 0
        rows.append(f"CUST-{i},{20 + i % 40},2024-{month:02d}-{day:02d},{churn}")
    content = "\n".join(rows)

    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    features = train_resp.json()[0]["feature_columns_json"]
    assert "signup_date" not in features
    assert "age" in features


def test_dataset_profile_flags_id_like_column(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    detail = auth_client.get(f"/api/datasets/{dataset_id}")
    assert detail.status_code == 200
    columns = {c["name"]: c for c in detail.json()["columns"]}
    assert columns["customer_id"]["is_id_like"] is True
    assert columns["age"]["is_id_like"] is False


def test_dataset_detail_suggests_target_column(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    detail = auth_client.get(f"/api/datasets/{dataset_id}")
    assert detail.status_code == 200
    assert detail.json()["suggested_target_column"] == "churn"


def test_training_without_feature_columns_excludes_id_like(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]
    assert "customer_id" not in model["feature_columns_json"]
    assert "age" in model["feature_columns_json"]


def test_training_respects_cv_folds(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={
            "dataset_id": dataset_id,
            "target_column": "churn",
            "cv_folds": 3,
            "models": ["logistic_regression"],
        },
    )
    assert train_resp.status_code == 201
    model = train_resp.json()[0]
    cv_scores = model["metrics_json"].get("cv_scores")
    assert cv_scores is not None
    assert len(cv_scores) == 3


def test_model_detail_includes_feature_schema(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    model_id = train_resp.json()[0]["id"]

    detail = auth_client.get(f"/api/models/{model_id}")
    assert detail.status_code == 200
    schema = detail.json()["feature_schema_json"]
    assert schema is not None
    numeric_fields = [f for f in schema if f["type"] == "numeric"]
    assert len(numeric_fields) > 0
    assert all("default" in f for f in numeric_fields)


def test_predict_returns_instance_contributions_for_linear_model(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    model_id = train_resp.json()[0]["id"]

    pred_resp = auth_client.post(
        "/api/predictions",
        json={
            "model_id": model_id,
            "features": {"age": 30, "monthly_spend": 100, "purchase_frequency": 2},
        },
    )
    assert pred_resp.status_code == 201
    contributions = pred_resp.json()["output_json"]["contributions"]
    assert contributions["type"] == "instance_contribution"
    assert len(contributions["items"]) > 0


def test_predict_falls_back_to_global_importance_for_tree_model(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["random_forest"]},
    )
    model_id = train_resp.json()[0]["id"]

    pred_resp = auth_client.post(
        "/api/predictions",
        json={
            "model_id": model_id,
            "features": {"age": 30, "monthly_spend": 100, "purchase_frequency": 2},
        },
    )
    assert pred_resp.status_code == 201
    contributions = pred_resp.json()["output_json"]["contributions"]
    assert contributions["type"] == "global_importance"


def test_batch_prediction(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    model_id = train_resp.json()[0]["id"]

    batch_csv = "age,monthly_spend,purchase_frequency\n25,100,2\n40,450,9\n"
    batch_resp = auth_client.post(
        "/api/predictions/batch",
        data={"model_id": model_id},
        files={"file": ("batch.csv", io.BytesIO(batch_csv.encode()), "text/csv")},
    )
    assert batch_resp.status_code == 200
    body = batch_resp.json()
    assert body["row_count"] == 2
    assert all("prediction" in row for row in body["results"])


def test_batch_prediction_rejects_missing_columns(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _churn_csv_with_id())
    dataset_id = upload_resp.json()["id"]

    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    model_id = train_resp.json()[0]["id"]

    batch_csv = "age\n25\n40\n"
    batch_resp = auth_client.post(
        "/api/predictions/batch",
        data={"model_id": model_id},
        files={"file": ("batch.csv", io.BytesIO(batch_csv.encode()), "text/csv")},
    )
    assert batch_resp.status_code == 422


def test_quality_endpoint_reports_missing_values(auth_client):
    project_id = create_project(auth_client)
    content = "age,income\n25,50000\n,60000\n35,\n40,70000\n"
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.get(f"/api/datasets/{dataset_id}/quality")
    assert resp.status_code == 200
    issues = resp.json()
    assert any(i["type"] == "missing_values" for i in issues)
    missing_issue = next(i for i in issues if i["type"] == "missing_values" and i["column"] == "age")
    assert missing_issue["recommended_fix"]["strategy"] == "median"


def test_quality_endpoint_reports_outliers_independently_of_missing_values(auth_client):
    """Regression test: outlier detection was accidentally nested inside the
    'if missing == 0: continue' branch, so a column with outliers but ZERO missing values
    never got checked for outliers at all. 'amount' below has no missing values but a
    clear outlier (5000 among values around 20-40)."""
    rows = ["amount"]
    rows += [str(20 + (i % 10)) for i in range(30)]
    rows.append("5000")
    content = "\n".join(rows)

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.get(f"/api/datasets/{dataset_id}/quality")
    assert resp.status_code == 200
    issues = resp.json()
    assert not any(i["type"] == "missing_values" for i in issues)
    assert any(i["type"] == "outliers" and i["column"] == "amount" for i in issues)


def test_quality_endpoint_reports_invalid_dates_and_impossible_values(auth_client):
    rows = ["order_date,delivery_minutes"]
    valid_dates = [f"2024-01-{d:02d}" for d in range(1, 21)]
    for i, d in enumerate(valid_dates):
        minutes = 30 if i != 5 else -2  # one rare, impossible negative duration
        rows.append(f"{d},{minutes}")
    rows.append("not-a-real-date,25")
    rows.append("also-bad,40")
    content = "\n".join(rows)

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.get(f"/api/datasets/{dataset_id}/quality")
    assert resp.status_code == 200
    issues = resp.json()

    invalid_date_issue = next(i for i in issues if i["type"] == "invalid_dates")
    assert invalid_date_issue["column"] == "order_date"
    assert invalid_date_issue["affected_count"] == 2

    impossible_issue = next(i for i in issues if i["type"] == "impossible_values")
    assert impossible_issue["column"] == "delivery_minutes"
    assert impossible_issue["affected_count"] == 1


def test_cleaning_is_reproducible_from_original_not_compounded(auth_client):
    project_id = create_project(auth_client)
    content = "age,income\n25,50000\n25,50000\n,60000\n35,\n"
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    first = auth_client.post(
        f"/api/analysis/{dataset_id}/clean",
        json={"missing_strategy": "auto", "remove_duplicates": True},
    )
    assert first.status_code == 200
    assert first.json()["cleaning_version"] == 1
    assert first.json()["missing_values"] == 0

    # Re-cleaning with a different strategy should still start from the ORIGINAL upload
    # (2 duplicate rows again), not from the already-cleaned, deduplicated file.
    second = auth_client.post(
        f"/api/analysis/{dataset_id}/clean",
        json={"missing_strategy": "drop", "remove_duplicates": True},
    )
    assert second.status_code == 200
    assert second.json()["cleaning_version"] == 2
    # 4 rows -> 1 exact duplicate removed -> 2 rows each missing one column -> both dropped,
    # leaving only the single fully-complete row. Proves this re-clean started fresh from the
    # ORIGINAL upload (still has the duplicate to remove) rather than the already-cleaned file.
    assert second.json()["row_count"] == 1


def test_sample_dataset_endpoint_creates_real_dataset(auth_client):
    project_id = create_project(auth_client)
    resp = auth_client.post(f"/api/projects/{project_id}/datasets/sample")
    assert resp.status_code == 201
    body = resp.json()
    assert body["row_count"] > 0
    assert body["column_count"] > 0
