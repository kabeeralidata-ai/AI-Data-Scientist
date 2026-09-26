"""Benchmark checks for coffee_shop_transactions.csv — a transaction log. Spec (verbatim):
  detected as TRANSACTION LOG; plan = revenue analytics, RFM segments, return
  prediction, forecast.
  No target chosen from unit_price_pkr/total_pkr/discount_pkr.
  6 invalid dates flagged and excluded from date range and charts (range ends 2026-03-31).
  Missing customer_id labeled "Walk-in / Unknown", never filled.
  Negative quantities reported as refunds and kept.
  1,150 identified customers.
  Predicted returners within +/-10% of 710 (answer_key_customer_returns.csv).
  Forecast MAPE vs answer_key_revenue_forecast.csv below 15%.

The two answer_key_*.csv files are read ONLY inside this function, to compare against
what the pipeline already produced — never fed into the pipeline itself.
"""

import os

import pandas as pd

from tests.benchmark.harness import CheckResult, DATA_DIR, dataset_path, poll_job, upload_dataset

DATASET_FILE = "coffee_shop_transactions.csv"
RETURNS_ANSWER_KEY = os.path.join(DATA_DIR, "answer_key_customer_returns.csv")
FORECAST_ANSWER_KEY = os.path.join(DATA_DIR, "answer_key_revenue_forecast.csv")


def run(client) -> list[CheckResult]:
    checks: list[CheckResult] = []
    project_id, dataset_id = upload_dataset(client, DATASET_FILE)

    start = client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start.status_code == 201, start.text
    job_id = start.json()["id"]
    job = poll_job(client, job_id, poll_attempts=60)
    checks.append(
        CheckResult("job pauses awaiting plan confirmation", job.get("status") == "awaiting_plan_confirmation", f"got {job.get('status')}")
    )

    result = job.get("result_json") or {}
    dataset_type = (result.get("dataset_type") or {}).get("type")
    checks.append(CheckResult("dataset type detected as transaction_log", dataset_type == "transaction_log", f"got {dataset_type}"))

    plan_keys = {a["key"] for a in (result.get("plan") or {}).get("analyses", []) if a.get("viable")}
    checks.append(
        CheckResult(
            "plan = revenue analytics, RFM, return prediction, forecast",
            plan_keys == {"revenue_analytics", "customer_rfm", "return_prediction", "sales_forecasting"},
            f"got {plan_keys}",
        )
    )

    formula_cols = {f["column"] for f in result.get("formula_columns", [])}
    checks.append(
        CheckResult(
            "total_pkr/discount_pkr detected as formula columns (never a target)",
            {"total_pkr", "discount_pkr"} <= formula_cols,
            f"formula_columns={formula_cols}",
        )
    )
    checks.append(CheckResult("no target column was chosen at all (transaction-log path)", result.get("target_column") is None, ""))

    confirm = client.post(f"/api/auto-analyze/{job_id}/confirm-plan")
    checks.append(CheckResult("confirm-plan accepted", confirm.status_code == 200, confirm.text[:300]))
    job2 = poll_job(client, job_id, poll_attempts=60)
    checks.append(CheckResult("job completes after plan confirmation", job2.get("status") == "completed", job2.get("error_message") or "completed"))

    result2 = job2.get("result_json") or {}
    checks.append(CheckResult("6 invalid dates flagged and excluded", result2.get("invalid_dates_excluded") == 6, f"got {result2.get('invalid_dates_excluded')}"))

    ta = result2.get("transaction_analysis") or {}
    revenue_months = {m["month"] for m in (ta.get("revenue_analytics") or {}).get("revenue_by_month", [])}
    checks.append(
        CheckResult("date range (revenue chart) ends 2026-03", (max(revenue_months) if revenue_months else None) == "2026-03", f"max month={max(revenue_months) if revenue_months else None}")
    )

    rfm = ta.get("customer_rfm") or {}
    checks.append(CheckResult("1,150 identified customers", rfm.get("total_identified_customers") == 1150, f"got {rfm.get('total_identified_customers')}"))

    # Missing customer_id -> "Walk-in / Unknown" label, never filled with a real ID —
    # verified against the actual cleaned dataset, not just the summary.
    from app.models.dataset import Dataset
    from app.services.cleaning_service import WALK_IN_LABEL
    from app.services.dataset_service import load_dataframe

    db = client.db_session_local()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        cleaned_df = load_dataframe(dataset)
    finally:
        db.close()
    walk_in_count = int((cleaned_df["customer_id"] == WALK_IN_LABEL).sum())
    checks.append(CheckResult("missing customer_id labeled 'Walk-in / Unknown'", walk_in_count > 1, f"{walk_in_count} rows labeled"))
    checks.append(CheckResult("negative quantities kept as refunds (not nulled)", int((cleaned_df["quantity"] < 0).sum()) > 0, f"{int((cleaned_df['quantity'] < 0).sum())} negative-quantity rows present"))

    rp = ta.get("return_prediction") or {}
    predicted = rp.get("predicted_will_return")
    if os.path.exists(RETURNS_ANSWER_KEY):
        answer = pd.read_csv(RETURNS_ANSWER_KEY)
        actual = int((answer["returned_apr_jun_2026"] == "Yes").sum())
        lower, upper = actual * 0.9, actual * 1.1
        checks.append(
            CheckResult(
                f"predicted returners within +/-10% of answer key ({actual})",
                predicted is not None and lower <= predicted <= upper,
                f"predicted={predicted}, actual={actual}, allowed=[{lower:.0f},{upper:.0f}]",
            )
        )
    else:
        checks.append(CheckResult("predicted returners vs answer key", False, "answer_key_customer_returns.csv not found"))

    fc = ta.get("sales_forecasting") or {}
    if os.path.exists(FORECAST_ANSWER_KEY) and fc.get("monthly_forecast"):
        answer = pd.read_csv(FORECAST_ANSWER_KEY)
        actual_by_month = dict(zip(answer["month"], answer["actual_revenue_pkr"]))
        errors = []
        for entry in fc["monthly_forecast"]:
            actual = actual_by_month.get(entry["month"])
            if actual:
                errors.append(abs(entry["point_forecast"] - actual) / actual * 100)
        mape = sum(errors) / len(errors) if errors else None
        checks.append(CheckResult("forecast MAPE vs answer key below 15%", mape is not None and mape < 15, f"MAPE={mape:.1f}%" if mape is not None else "no overlapping months"))
    else:
        checks.append(CheckResult("forecast MAPE vs answer key", False, "answer_key_revenue_forecast.csv not found or no forecast produced"))

    return checks
