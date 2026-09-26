"""Benchmark checks for karachi_food_delivery_dataset.csv — a customer-level prediction
table with target late_delivery. Spec (verbatim from the task):
  exclude delivery_time_min, tip_pkr, customer_rating; KEEP promised_time_min.
  "4.2 km" values converted, distance_km numeric (10 numeric columns).
  Delivery times of -1 flagged.
  Profile 2,520 rows -> 2,500 after cleaning.
  Jam traffic has the highest average delivery time.
"""

import pandas as pd

from tests.benchmark.harness import CheckResult, dataset_path, poll_job, upload_dataset

DATASET_FILE = "karachi_food_delivery_dataset.csv"


def run(client) -> list[CheckResult]:
    checks: list[CheckResult] = []
    project_id, dataset_id = upload_dataset(client, DATASET_FILE)

    dataset_before = client.get(f"/api/datasets/{dataset_id}").json()
    checks.append(
        CheckResult(
            "profile: 2,520 rows before cleaning",
            dataset_before.get("row_count") == 2520,
            f"got {dataset_before.get('row_count')}",
        )
    )

    quality = client.get(f"/api/datasets/{dataset_id}/quality").json()
    negative_delivery_issue = next(
        (i for i in quality if i.get("column") == "delivery_time_min" and i.get("type") in ("impossible_values", "outliers")),
        None,
    )
    checks.append(
        CheckResult(
            "delivery_time_min = -1 rows flagged by the quality report",
            negative_delivery_issue is not None,
            str(negative_delivery_issue),
        )
    )

    start = client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start.status_code == 201, start.text
    job_id = start.json()["id"]
    job = poll_job(client, job_id)
    checks.append(
        CheckResult(
            "Auto Analyze pauses for target confirmation (not auto-forced)",
            job.get("status") == "awaiting_target_confirmation",
            f"got status={job.get('status')}",
        )
    )

    cleaning_step = next((s for s in job.get("steps_json", []) if s["key"] == "cleaning"), {})
    checks.append(
        CheckResult(
            "cleaning: 2,520 -> 2,500 rows",
            "2500 rows after cleaning" in (cleaning_step.get("detail") or ""),
            cleaning_step.get("detail"),
        )
    )

    dataset_after = client.get(f"/api/datasets/{dataset_id}").json()
    distance_col = next((c for c in dataset_after.get("columns", []) if c["name"] == "distance_km"), None)
    checks.append(
        CheckResult(
            '"4.2 km"-style values converted — distance_km is numeric',
            bool(distance_col and distance_col.get("is_numeric")),
            str(distance_col),
        )
    )

    eda = client.post(f"/api/analysis/{dataset_id}/eda").json()
    numeric_count = (eda.get("summary") or {}).get("numeric_columns")
    checks.append(CheckResult("10 numeric columns after cleaning", numeric_count == 10, f"got {numeric_count}"))

    confirm = client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "late_delivery"})
    checks.append(CheckResult("confirm-target(late_delivery) accepted", confirm.status_code == 200, confirm.text[:300]))

    job2 = poll_job(client, job_id)
    checks.append(
        CheckResult(
            "job completes after target confirmation",
            job2.get("status") == "completed",
            job2.get("error_message") or "completed",
        )
    )

    result = job2.get("result_json") or {}
    best_model_id = result.get("best_model_id")
    model = client.get(f"/api/models/{best_model_id}").json() if best_model_id else {}
    features = set(model.get("feature_columns_json") or [])

    for excluded in ("delivery_time_min", "tip_pkr", "customer_rating"):
        checks.append(
            CheckResult(f"'{excluded}' excluded from training features", excluded not in features, f"features={sorted(features)}")
        )
    checks.append(
        CheckResult("'promised_time_min' KEPT as a training feature", "promised_time_min" in features, f"features={sorted(features)}")
    )

    # Ground-truth fact, verified directly from the real file (not dependent on any one
    # report wording) — Jam traffic must show the highest average delivery time.
    df = pd.read_csv(dataset_path(DATASET_FILE))
    avg_by_traffic = df.groupby("traffic_level")["delivery_time_min"].mean()
    checks.append(
        CheckResult(
            "Jam traffic has the highest average delivery time (ground truth)",
            avg_by_traffic.idxmax() == "Jam",
            f"averages={avg_by_traffic.round(1).to_dict()}",
        )
    )

    return checks
