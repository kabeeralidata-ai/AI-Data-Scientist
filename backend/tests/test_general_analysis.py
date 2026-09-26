"""Covers the 'no target column detected' path added in this task: Auto Analyze must
never simply fail the job when score_target_candidates() finds nothing — spec requirement
"If no target exists, allow analysis without supervised ML" instead of forcing every
dataset into classification/regression. Falls through to K-Means clustering when there's
enough numeric structure, or an EDA-only 'general' summary otherwise. Both real code paths
are exercised end-to-end (real dataset upload, real background job execution, real report
generation) — only dataset_service.score_target_candidates is monkeypatched, since forcing
a genuinely target-less dataset through the real (deliberately generous) scoring heuristic
is both harder to construct and less precise than directly testing this boundary."""

import io
import random


def create_project(client, name="General Analysis Project"):
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _numeric_dataset_csv(n=150):
    """Several genuinely-varying numeric columns with no target-like naming — realistic
    material for clustering."""
    random.seed(11)
    rows = ["sensor_id,reading_a,reading_b,reading_c"]
    for i in range(n):
        a = round(random.uniform(0, 100), 2)
        b = round(a * 0.5 + random.uniform(-10, 10), 2)
        c = round(random.uniform(-50, 50), 2)
        rows.append(f"SID-{i},{a},{b},{c}")
    return "\n".join(rows)


def _single_numeric_dataset_csv(n=60):
    """Only one numeric column (plus an ID) — not enough numeric structure to cluster
    (run_clustering_analysis requires >= 2 numeric columns), so this must fall all the way
    through to the EDA-only 'general' summary instead."""
    random.seed(3)
    rows = ["record_id,value"]
    for i in range(n):
        rows.append(f"REC-{i},{round(random.uniform(0, 10), 2)}")
    return "\n".join(rows)


def upload_csv(client, project_id, content):
    resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_auto_analyze_runs_clustering_when_no_target_is_detected(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.auto_analyze_service.dataset_service.score_target_candidates", lambda *a, **k: [])

    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _numeric_dataset_csv())

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start_resp.status_code == 201
    job_id = start_resp.json()["id"]

    # Phase 4 requirement: the plan is shown to the user (with a reason and a confidence
    # level per analysis, and an overall plan confidence) and must be explicitly confirmed
    # before anything runs — segmentation is no longer a silent auto-run.
    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "awaiting_plan_confirmation"
    plan = job["result_json"]["plan"]
    assert plan["dataset_type"] == "general"
    assert plan["confidence"] in ("high", "medium", "low", "none")
    segmentation = next(a for a in plan["analyses"] if a["key"] == "segmentation")
    assert segmentation["viable"] is True
    assert segmentation["confidence"] in ("high", "medium", "low")
    assert segmentation["reason"]

    confirm_resp = auth_client.post(f"/api/auto-analyze/{job_id}/confirm-plan")
    assert confirm_resp.status_code == 200

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "completed"  # never "failed" just because there's no target

    result = job["result_json"]
    assert result["target_column"] is None
    assert result["best_model_id"] is None
    assert result["analysis_type"] == "clustering"
    assert result["clusters"] is not None
    assert result["clusters"]["k"] >= 2
    assert len(result["clusters"]["clusters"]) == result["clusters"]["k"]
    assert set(result["clusters"]["features_used"]) <= {"reading_a", "reading_b", "reading_c"}
    assert "sensor_id" not in result["clusters"]["features_used"]  # ID-like column never clustered on

    # Every cluster's row count adds up to the total rows clustered.
    assert sum(c["size"] for c in result["clusters"]["clusters"]) == result["clusters"]["rows_clustered"]

    # The step list must be honest: never silently "completed" as if a model was trained.
    training_step = next(s for s in job["steps_json"] if s["key"] == "training")
    assert "cluster" in training_step["detail"].lower()

    # A real report was generated, containing an actual Cluster Analysis section — not a
    # crash, not a blank/fake report.
    assert result["report_id"]
    report_resp = auth_client.get(f"/api/reports/{result['report_id']}/download")
    assert report_resp.status_code == 200
    assert report_resp.content[:4] == b"%PDF"

    preview_resp = auth_client.get(f"/api/reports/{result['report_id']}/preview")
    assert preview_resp.status_code == 200
    assert "Cluster Analysis" in preview_resp.text
    assert "{'" not in preview_resp.text  # no raw Python dict dump leaked into the report


def test_auto_analyze_falls_back_to_general_analysis_when_clustering_is_not_viable(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.auto_analyze_service.dataset_service.score_target_candidates", lambda *a, **k: [])

    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _single_numeric_dataset_csv())

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]

    # Even when nothing is viable, the plan still pauses for confirmation — the user gets
    # a chance to override with their own target rather than silently landing on an
    # EDA-only report with no say in the matter.
    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "awaiting_plan_confirmation"
    plan = job["result_json"]["plan"]
    segmentation = next(a for a in plan["analyses"] if a["key"] == "segmentation")
    assert segmentation["viable"] is False
    assert segmentation["confidence"] == "none"
    assert segmentation["reason"]

    confirm_resp = auth_client.post(f"/api/auto-analyze/{job_id}/confirm-plan")
    assert confirm_resp.status_code == 200

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "completed"

    result = job["result_json"]
    assert result["target_column"] is None
    assert result["analysis_type"] == "general"
    assert result["clusters"] is None

    training_step = next(s for s in job["steps_json"] if s["key"] == "training")
    assert training_step["status"] == "skipped"

    # Still produces a real report (dataset + data quality + EDA sections only), and
    # explains WHY no model was trained instead of a bare "no model available" that could
    # read as a failure.
    assert result["report_id"]
    report_resp = auth_client.get(f"/api/reports/{result['report_id']}/download")
    assert report_resp.status_code == 200
    assert report_resp.content[:4] == b"%PDF"

    preview_resp = auth_client.get(f"/api/reports/{result['report_id']}/preview")
    assert preview_resp.status_code == 200
    assert "No target column was confidently detected" in preview_resp.text
    assert "{'" not in preview_resp.text


def test_run_clustering_analysis_returns_none_for_too_few_numeric_columns():
    """Direct unit check on the clustering function's own honesty guard — never fabricates
    clusters when there isn't real numeric structure to cluster on."""
    import pandas as pd

    from app.services.ml_service import run_clustering_analysis

    class FakeDataset:
        profile_json = {"columns": [{"name": "value", "is_numeric": True, "is_id_like": False}]}

    df = pd.DataFrame({"value": range(100)})
    assert run_clustering_analysis(FakeDataset(), df) is None


def test_run_clustering_analysis_returns_none_for_too_few_rows():
    import pandas as pd

    from app.services.ml_service import run_clustering_analysis

    class FakeDataset:
        profile_json = {
            "columns": [
                {"name": "a", "is_numeric": True, "is_id_like": False},
                {"name": "b", "is_numeric": True, "is_id_like": False},
            ]
        }

    df = pd.DataFrame({"a": range(5), "b": range(5)})
    assert run_clustering_analysis(FakeDataset(), df) is None


def test_run_clustering_analysis_excludes_id_like_columns():
    import pandas as pd

    from app.services.ml_service import run_clustering_analysis

    class FakeDataset:
        profile_json = {
            "columns": [
                {"name": "id", "is_numeric": True, "is_id_like": True},
                {"name": "a", "is_numeric": True, "is_id_like": False},
                {"name": "b", "is_numeric": True, "is_id_like": False},
            ]
        }

    random.seed(1)
    df = pd.DataFrame(
        {
            "id": range(200),
            "a": [random.uniform(0, 10) for _ in range(200)],
            "b": [random.uniform(0, 10) for _ in range(200)],
        }
    )
    result = run_clustering_analysis(FakeDataset(), df)
    assert result is not None
    assert "id" not in result["features_used"]


def test_recommend_model_keys_excludes_svm_for_large_datasets():
    from app.services.ml_service import recommend_model_keys

    small = recommend_model_keys("classification", 500)
    large = recommend_model_keys("classification", 50_000)
    assert "svm" in small
    assert "svm" not in large
    assert "knn" not in large


def test_classification_class_weight_detects_imbalance():
    import pandas as pd

    from app.services.ml_service import _classification_class_weight

    balanced = pd.Series([0] * 50 + [1] * 50)
    imbalanced = pd.Series([0] * 95 + [1] * 5)
    assert _classification_class_weight(balanced) is None
    assert _classification_class_weight(imbalanced) == "balanced"
