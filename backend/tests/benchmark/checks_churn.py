"""Benchmark checks for customer_churn_dataset.csv (NOT YET PRESENT in backend/tests/data/
— see docs/PROGRESS.md). Spec (verbatim):
  classification, target churn (~35% churned).
  Exclude customer_id. customer_id and signup_date never in prediction inputs.
  Top drivers include contract type, days since last purchase, support tickets, satisfaction.
  Score realistic (below 0.99) and above baseline.

Written ahead of the file's arrival so the benchmark "just works" the moment it's added —
see harness.run_dataset_benchmark(), which skips (not fakes) a dataset with no file.
"""

import pandas as pd

from tests.benchmark.harness import CheckResult, dataset_path, poll_job, upload_dataset

DATASET_FILE = "customer_churn_dataset.csv"
TARGET_COLUMN = "churn"
EXPECTED_TOP_DRIVER_HINTS = ("contract", "days_since_last_purchase", "days_since", "support_ticket", "satisfaction")


def run(client) -> list[CheckResult]:
    checks: list[CheckResult] = []
    project_id, dataset_id = upload_dataset(client, DATASET_FILE)

    df = pd.read_csv(dataset_path(DATASET_FILE))
    if TARGET_COLUMN in df.columns:
        churn_rate = df[TARGET_COLUMN].astype(str).str.lower().isin(("1", "true", "yes")).mean()
        checks.append(CheckResult("~35% churned (ground truth)", 0.25 <= churn_rate <= 0.45, f"got {churn_rate:.1%}"))

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

    checks.append(CheckResult("customer_id excluded from features", "customer_id" not in features, f"features={sorted(features)}"))
    checks.append(CheckResult("signup_date (raw) excluded from features", "signup_date" not in features, f"features={sorted(features)}"))

    importance = model.get("feature_importance_json") or []
    top_names = {f["name"].lower() for f in importance[:6]}
    matched_drivers = [hint for hint in EXPECTED_TOP_DRIVER_HINTS if any(hint in n for n in top_names)]
    checks.append(
        CheckResult(
            "top drivers include contract type / recency / support tickets / satisfaction",
            len(matched_drivers) >= 2,
            f"top features={top_names}, matched={matched_drivers}",
        )
    )

    score = metrics.get("f1") or metrics.get("accuracy")
    baseline_score = (metrics.get("baseline") or {}).get("baseline_score")
    checks.append(CheckResult("score is realistic (< 0.99)", score is not None and score < 0.99, f"score={score}"))
    checks.append(
        CheckResult(
            "score beats baseline",
            score is not None and baseline_score is not None and score > baseline_score,
            f"score={score}, baseline={baseline_score}",
        )
    )
    checks.append(CheckResult("model not flagged weak", not metrics.get("is_weak"), metrics.get("weak_reason") or "not weak"))

    return checks
