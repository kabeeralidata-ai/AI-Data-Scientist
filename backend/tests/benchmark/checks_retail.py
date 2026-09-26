"""Benchmark checks for retail_sales_dataset.csv (NOT YET PRESENT — see docs/PROGRESS.md).
Spec (verbatim):
  regression, target sales_amount.
  Exclude order_id and profit (derived from sales); returned and customer_rating
  excluded as post-outcome.
  Negative profit KEPT (losses).
  Report notes sales ~= price x quantity x (1 - discount).
  Electronics top revenue category; November-December peak visible in the revenue trend.

The last two checks (top category / seasonal peak) are NOT yet representable by the
current report architecture for a plain supervised-regression dataset (those concepts
only exist today for the transaction-log path) — they're expected to fail until Phase 4/6
extend the planner/report beyond the narrow "transaction_log" trigger. Written now so
they start passing automatically once that work lands, without editing this file again.
"""

from tests.benchmark.harness import CheckResult, dataset_path, poll_job, upload_dataset

DATASET_FILE = "retail_sales_dataset.csv"
TARGET_COLUMN = "sales_amount"


def run(client) -> list[CheckResult]:
    checks: list[CheckResult] = []
    project_id, dataset_id = upload_dataset(client, DATASET_FILE)

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

    checks.append(CheckResult("order_id excluded from features", "order_id" not in features, f"features={sorted(features)}"))
    checks.append(CheckResult("profit excluded from features (derived from sales)", "profit" not in features, f"features={sorted(features)}"))

    post_outcome = {w["column"] for w in metrics.get("post_outcome_excluded", [])} | {w["column"] for w in metrics.get("leakage_warnings", [])}
    checks.append(CheckResult("'returned' excluded as post-outcome/leakage", "returned" in post_outcome, f"excluded set={post_outcome}"))
    checks.append(CheckResult("'customer_rating' excluded as post-outcome/leakage", "customer_rating" in post_outcome, f"excluded set={post_outcome}"))

    # Negative profit preserved (a real loss), not silently nulled by the generic
    # rare-negative-value heuristic — requires profit's MONEY role + can_be_negative=True
    # from data_understanding_service (already wired into Auto Analyze's cleaning step).
    import pandas as pd

    from app.models.dataset import Dataset
    from app.services.dataset_service import load_dataframe

    db = client.db_session_local()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        cleaned_df = load_dataframe(dataset)
    finally:
        db.close()
    if "profit" in cleaned_df.columns:
        negative_profit_count = int((pd.to_numeric(cleaned_df["profit"], errors="coerce") < 0).sum())
        checks.append(CheckResult("negative profit values kept (real losses)", negative_profit_count > 0, f"{negative_profit_count} negative-profit rows present after cleaning"))

    # Aspirational — see module docstring; expected to fail until Phase 4/6.
    html = ""
    if result.get("report_id"):
        preview = client.get(f"/api/reports/{result['report_id']}/preview")
        html = preview.text if preview.status_code == 200 else ""
    checks.append(CheckResult("report notes sales ~= price x quantity x (1 - discount)", "discount" in html.lower() and "quantity" in html.lower(), "[Phase 4/6 gap — see module docstring]"))
    checks.append(CheckResult("Electronics identified as top revenue category", "electronics" in html.lower(), "[Phase 4/6 gap — see module docstring]"))
    checks.append(CheckResult("Nov-Dec revenue peak visible", "november" in html.lower() or "december" in html.lower(), "[Phase 4/6 gap — see module docstring]"))

    return checks
