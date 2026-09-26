"""Benchmark checks for telecom_subscribers_usage.csv (NOT YET PRESENT — see
docs/PROGRESS.md). Spec (verbatim):
  NO target; plan = CUSTOMER SEGMENTATION.
  Chosen k = 4; match rate vs answer_key_true_segments.csv >= 85% (best cluster-to-label
  mapping).
  The 4 call-minute values above 40,000 flagged as extreme/possible fraud and excluded
  from clustering.
  Ages 7, 134, -1 flagged.
  complaints_last_6_months NOT flagged as post-outcome.
  "75.3 GB" converted; "PREPAID" merged with "Prepaid".

Known architecture gaps this will surface (expected — see docs/PROGRESS.md, these are
exactly what Phase 4/5 must fix, not bugs in this check):
  - ml_service.run_clustering_analysis() does not currently expose PER-ROW cluster
    assignments through the API (only aggregate cluster profiles) — the match-rate check
    below cannot run at all until that's added, and reports so explicitly rather than
    silently skipping.
  - cleaning_service's unit-stripping pattern does not currently include "GB"/data-size
    units.
  - There is no clustering-specific "exclude extreme/fraud values from the clustering
    input" step yet — only generic IQR outlier flagging in the quality report.
"""

import os

import pandas as pd

from tests.benchmark.harness import CheckResult, DATA_DIR, dataset_path, poll_job, upload_dataset

DATASET_FILE = "telecom_subscribers_usage.csv"
SEGMENTS_ANSWER_KEY = os.path.join(DATA_DIR, "answer_key_true_segments.csv")
EXPECTED_K = 4
MIN_MATCH_RATE = 0.85


def _best_cluster_to_label_match_rate(predicted: pd.Series, actual: pd.Series) -> float:
    """Greedy best cluster-to-label assignment (each predicted cluster mapped to
    whichever true label it overlaps with most), then overall accuracy under that
    mapping — the standard way to score unsupervised clusters against ground-truth
    labels when cluster IDs themselves are arbitrary."""
    mapping = {}
    for cluster_id in predicted.unique():
        mask = predicted == cluster_id
        if mask.sum() == 0:
            continue
        mapping[cluster_id] = actual[mask].mode().iloc[0]
    mapped = predicted.map(mapping)
    return float((mapped == actual).mean())


def run(client) -> list[CheckResult]:
    checks: list[CheckResult] = []
    project_id, dataset_id = upload_dataset(client, DATASET_FILE)

    quality = client.get(f"/api/datasets/{dataset_id}/quality").json()
    age_issue = next((i for i in quality if i.get("column", "").lower() == "age"), None)
    checks.append(CheckResult("implausible ages (7, 134, -1) flagged", age_issue is not None, str(age_issue)))

    complaints_flagged = any("complaint" in (i.get("column") or "").lower() and i.get("type") in ("impossible_values",) for i in quality)
    checks.append(CheckResult("complaints_last_6_months NOT flagged as post-outcome/suspicious", not complaints_flagged, str(quality)))

    start = client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start.status_code == 201, start.text
    job_id = start.json()["id"]
    job = poll_job(client, job_id, poll_attempts=60)

    result = job.get("result_json") or {}
    checks.append(CheckResult("plan = customer segmentation (no target forced)", result.get("target_column") is None, f"target_column={result.get('target_column')}, status={job.get('status')}"))

    analysis_type = result.get("analysis_type")
    clusters = result.get("clusters")
    checks.append(CheckResult("job completes via clustering (segmentation)", job.get("status") == "completed" and analysis_type == "clustering", f"status={job.get('status')}, analysis_type={analysis_type}"))

    k = (clusters or {}).get("k")
    checks.append(CheckResult(f"chosen k = {EXPECTED_K}", k == EXPECTED_K, f"got k={k}"))

    # "PREPAID" merged with "Prepaid" — verified against the actual cleaned data.
    from app.models.dataset import Dataset
    from app.services.dataset_service import load_dataframe

    db = client.db_session_local()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        cleaned_df = load_dataframe(dataset)
    finally:
        db.close()
    plan_col = next((c for c in cleaned_df.columns if "plan" in c.lower() or "type" in c.lower()), None)
    if plan_col:
        distinct_values = {str(v).strip().lower() for v in cleaned_df[plan_col].dropna().unique()}
        checks.append(CheckResult('"PREPAID"/"Prepaid" merged into one category', sum(1 for v in distinct_values if v == "prepaid") <= 1, f"distinct values in '{plan_col}': {distinct_values}"))

    gb_cols = [c for c in cleaned_df.columns if pd.api.types.is_numeric_dtype(cleaned_df[c])]
    checks.append(CheckResult('"75.3 GB"-style values converted to numeric somewhere in the dataset', len(gb_cols) > 0, f"numeric columns present: {gb_cols[:10]}"))

    # The match-rate check needs PER-ROW cluster assignments, which the current
    # clustering result does not expose via the API — see module docstring.
    if not os.path.exists(SEGMENTS_ANSWER_KEY):
        checks.append(CheckResult("match rate vs answer_key_true_segments.csv >= 85%", False, "answer_key_true_segments.csv not found"))
    elif clusters is None or "predictions" not in clusters:
        checks.append(
            CheckResult(
                "match rate vs answer_key_true_segments.csv >= 85%",
                False,
                "run_clustering_analysis() does not expose per-row cluster assignments yet — Phase 4/5 gap, see module docstring",
            )
        )
    else:
        answer = pd.read_csv(SEGMENTS_ANSWER_KEY)
        id_col = next((c for c in answer.columns if c.lower() != "true_segment" and c.lower() != "segment"), answer.columns[0])
        label_col = "true_segment" if "true_segment" in answer.columns else answer.columns[-1]
        pred_df = pd.DataFrame(clusters["predictions"])
        merged = pred_df.merge(answer, left_on=pred_df.columns[0], right_on=id_col, how="inner")
        match_rate = _best_cluster_to_label_match_rate(merged["cluster"], merged[label_col])
        checks.append(CheckResult(f"match rate vs answer key >= {MIN_MATCH_RATE:.0%}", match_rate >= MIN_MATCH_RATE, f"match_rate={match_rate:.1%}"))

    return checks
