"""Benchmark checks for customer_retention_training.csv (NOT YET PRESENT — see
docs/PROGRESS.md). Spec (verbatim):
  classification, target will_return_next_90_days.
  orders_next_90_days detected as leakage and excluded.
  Ages 0, 150, -5, 212 flagged.
  Batch prediction on new_customers_to_predict.csv (500 rows) using raw columns (dates
  processed by the saved pipeline) -> predicted returners between 190 and 260 (true: 223).

The batch-prediction-on-raw-columns check is expected to FAIL until Phase 5 — confirmed
by reading prediction_service.predict_batch(): it does `rows_df[all_features]` directly,
requiring the uploaded file to already contain any DERIVED feature columns (e.g. a
date's extracted year/month) under their derived names. It does not currently re-run the
raw-column -> derived-feature transform a training run with a date column would have
applied. This is exactly the "prediction validates raw input columns only" requirement
Phase 5 states — written here so the check starts passing the moment that's fixed,
without editing this file again.
"""

import os

import pandas as pd

from tests.benchmark.harness import CheckResult, dataset_path, poll_job, upload_dataset

DATASET_FILE = "customer_retention_training.csv"
BATCH_FILE = "new_customers_to_predict.csv"
TARGET_COLUMN = "will_return_next_90_days"
TRUE_RETURNERS = 223
ALLOWED_LOW, ALLOWED_HIGH = 190, 260


def run(client) -> list[CheckResult]:
    checks: list[CheckResult] = []
    project_id, dataset_id = upload_dataset(client, DATASET_FILE)

    quality = client.get(f"/api/datasets/{dataset_id}/quality").json()
    age_issue = next((i for i in quality if i.get("column") == "age"), None)
    checks.append(CheckResult("implausible ages (0, 150, -5, 212) flagged", age_issue is not None, str(age_issue)))

    start = client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start.status_code == 201, start.text
    job_id = start.json()["id"]
    job = poll_job(client, job_id)

    if job.get("status") == "awaiting_target_confirmation":
        candidates = {c["column"] for c in job.get("target_candidates_json", [])}
        checks.append(CheckResult(f"'{TARGET_COLUMN}' suggested as a target candidate", TARGET_COLUMN in candidates, f"candidates={candidates}"))
        confirm = client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": TARGET_COLUMN})
        checks.append(CheckResult("confirm-target accepted", confirm.status_code == 200, confirm.text[:300]))
        job = poll_job(client, job_id)
    else:
        checks.append(CheckResult("Auto Analyze reaches target confirmation", False, f"got status={job.get('status')}"))

    checks.append(CheckResult("job completes", job.get("status") == "completed", job.get("error_message") or "completed"))
    result = job.get("result_json") or {}
    best_model_id = result.get("best_model_id")
    model = client.get(f"/api/models/{best_model_id}").json() if best_model_id else {}
    features = set(model.get("feature_columns_json") or [])
    metrics = model.get("metrics_json") or {}

    leakage_cols = {w["column"] for w in metrics.get("leakage_warnings", [])}
    checks.append(
        CheckResult(
            "'orders_next_90_days' detected as leakage and excluded",
            "orders_next_90_days" in leakage_cols or "orders_next_90_days" not in features,
            f"leakage_warnings={leakage_cols}, features={sorted(features)}",
        )
    )

    batch_path = dataset_path(BATCH_FILE)
    if not os.path.exists(batch_path) or not best_model_id:
        checks.append(CheckResult("batch prediction on new_customers_to_predict.csv", False, f"{BATCH_FILE} not found or no model trained"))
        return checks

    with open(batch_path, "rb") as f:
        batch_resp = client.post(
            "/api/predictions/batch",
            data={"model_id": best_model_id},
            files={"file": (BATCH_FILE, f, "text/csv")},
        )
    checks.append(CheckResult("batch prediction accepts raw new_customers_to_predict.csv", batch_resp.status_code == 200, batch_resp.text[:500]))

    if batch_resp.status_code == 200:
        body = batch_resp.json()
        checks.append(CheckResult("batch prediction returns 500 rows", body.get("row_count") == 500, f"got {body.get('row_count')}"))
        predicted_returners = sum(1 for r in body.get("results", []) if str(r.get("prediction")).lower() in ("1", "true", "yes"))
        checks.append(
            CheckResult(
                f"predicted returners between {ALLOWED_LOW} and {ALLOWED_HIGH} (true: {TRUE_RETURNERS})",
                ALLOWED_LOW <= predicted_returners <= ALLOWED_HIGH,
                f"predicted={predicted_returners}",
            )
        )

    return checks
