import io
import random


def create_project(client, name="Auto Analyze Project"):
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _csv_with_duplicates(n=100, dup_count=5):
    """n unique rows plus dup_count exact duplicates of the first few, so the original
    upload has n + dup_count rows and cleaning removes exactly dup_count of them."""
    random.seed(9)
    base_rows = []
    for i in range(n):
        age = random.randint(18, 70)
        income = random.randint(20000, 90000)
        churn = 1 if income < 40000 else 0
        base_rows.append(f"{age},{income},{churn}")
    all_rows = base_rows + base_rows[:dup_count]
    return "\n".join(["age,income,churn"] + all_rows)


def _make_classification_csv(n=120):
    random.seed(7)
    rows = ["customer_id,age,monthly_spend,purchase_frequency,churn"]
    for i in range(n):
        age = random.randint(18, 70)
        spend = round(random.uniform(10, 500), 2)
        freq = random.randint(0, 20)
        churn = 1 if spend < 150 and freq < 5 else 0
        rows.append(f"CID-{2000+i},{age},{spend},{freq},{churn}")
    return "\n".join(rows)


def upload_csv(client, project_id, content):
    return client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )


def test_auto_analyze_runs_full_pipeline_end_to_end(auth_client):
    """FastAPI's TestClient executes BackgroundTasks synchronously before the request
    returns, so by the time each request responds, that phase of the pipeline has already
    run — no polling loop needed in this test. The first run for a project pauses after
    target detection for the user to confirm the suggested target."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(
        f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id}
    )
    assert start_resp.status_code == 201
    job_id = start_resp.json()["id"]

    paused = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert paused["status"] == "awaiting_target_confirmation"
    assert paused["target_candidates_json"][0]["column"] == "churn"
    for key in ("profile", "cleaning", "eda", "target_detection"):
        step = next(s for s in paused["steps_json"] if s["key"] == key)
        assert step["status"] == "completed"
    for key in ("training", "comparison", "ai_insights", "report"):
        step = next(s for s in paused["steps_json"] if s["key"] == key)
        assert step["status"] == "pending"

    confirm_resp = auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "churn"})
    assert confirm_resp.status_code == 200

    status_resp = auth_client.get(f"/api/auto-analyze/{job_id}")
    assert status_resp.status_code == 200
    job = status_resp.json()

    assert job["status"] == "completed", job.get("error_message")
    assert job["confirmed_target_column"] == "churn"
    for step in job["steps_json"]:
        assert step["status"] in ("completed", "warning")

    result = job["result_json"]
    assert result["target_column"] == "churn"
    assert result["best_model_id"]
    assert result["problem_type"] == "classification"
    assert "report_id" in result

    # The trained model and report should be real, queryable resources.
    model_resp = auth_client.get(f"/api/models/{result['best_model_id']}")
    assert model_resp.status_code == 200
    assert model_resp.json()["is_best"] is True

    report_resp = auth_client.get(f"/api/reports/{result['report_id']}/download")
    assert report_resp.status_code == 200
    assert report_resp.content[:4] == b"%PDF"


def test_auto_analyze_marks_ai_insights_as_warning_not_completed_when_ollama_unavailable(auth_client):
    """Honest step status: a step that completed with a real issue (Ollama unreachable in
    the test environment) must show 'warning', never a plain 'completed' that would imply
    nothing went wrong."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]
    auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "churn"})

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "completed"

    ai_step = next(s for s in job["steps_json"] if s["key"] == "ai_insights")
    assert ai_step["status"] == "warning"
    assert job["result_json"]["ai_available"] is False


def test_auto_analyze_ai_step_reports_the_specific_gemini_failure_reason(auth_client, monkeypatch):
    """Regression test: the AutoAnalyzePanel UI used to show a hard-coded 'AI narrative
    unavailable (... unreachable)' no matter what actually failed — even a rate limit or
    an invalid key. result_json.ai_reason must carry the real classified reason so the
    frontend can show a specific message, and the step's own detail text must not be a
    generic 'unavailable' string either."""
    from app.services.gemini_service import GeminiUnavailableError

    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")

    def failing_gemini(context):
        raise GeminiUnavailableError("simulated quota exhaustion", reason="rate_limited")

    monkeypatch.setattr("app.services.ai_service.gemini_service.generate_insight", failing_gemini)

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]
    auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "churn"})

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["result_json"]["ai_available"] is False
    assert job["result_json"]["ai_reason"] == "rate_limited"

    ai_step = next(s for s in job["steps_json"] if s["key"] == "ai_insights")
    assert ai_step["status"] == "warning"
    assert "rate limit" in ai_step["detail"].lower()
    assert "unreachable" not in ai_step["detail"].lower()


def test_retry_ai_insights_endpoint_reruns_only_that_step(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]
    auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "churn"})

    before = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert before["status"] == "completed"
    report_id_before = before["result_json"]["report_id"]

    retry_resp = auth_client.post(f"/api/auto-analyze/{job_id}/retry-ai-insights")
    assert retry_resp.status_code == 200

    after = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    # Ollama is genuinely unreachable in this test environment, so the retry honestly
    # stays a warning rather than flipping to "completed" — but the rest of the job
    # (training/report) must be untouched by the retry.
    ai_step = next(s for s in after["steps_json"] if s["key"] == "ai_insights")
    assert ai_step["status"] == "warning"
    assert after["result_json"]["report_id"] == report_id_before


def test_retry_ai_insights_rejects_a_job_that_is_not_completed(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]
    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]

    # Still awaiting_target_confirmation — not completed yet.
    retry_resp = auth_client.post(f"/api/auto-analyze/{job_id}/retry-ai-insights")
    assert retry_resp.status_code == 409


def _make_completed_job(db_session, project_id, dataset_id, result_json):
    """Directly constructs a COMPLETED AutoAnalyzeJob row with the given result_json —
    used to test retry_ai_insights()'s dispatch logic for clustering/transaction-log jobs
    without re-running their (expensive, already-covered-elsewhere) full pipelines."""
    import uuid

    from app.models.auto_analyze_job import AutoAnalyzeJob, AutoAnalyzeJobStatus

    job = AutoAnalyzeJob(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        dataset_id=uuid.UUID(dataset_id),
        status=AutoAnalyzeJobStatus.COMPLETED,
        steps_json=None,
        result_json=result_json,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_retry_ai_insights_works_for_a_clustering_job_not_just_supervised(auth_client, monkeypatch, db_session):
    """Previously retry_ai_insights() checked job.result_json.get("best_model_id") and
    silently returned early for a clustering job (no model), so the Retry button shown on
    the UI whenever the AI step is a warning did nothing at all for clustering — no
    error, no retried call, nothing. This directly exercises that clustering must now
    actually attempt a fresh call and reflect the result."""
    from app.models.ai_insight_cache import AIInsightCache
    from app.services import auto_analyze_service

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    job = _make_completed_job(
        db_session,
        project_id,
        dataset_id,
        result_json={
            "analysis_type": "clustering",
            "best_model_id": None,
            "clusters": {"k": 2, "clusters": [], "features_used": ["age", "monthly_spend"], "silhouette_score": 0.4},
            "ai_insight": None,
            "ai_available": False,
            "ai_reason": "rate_limited",
        },
    )

    monkeypatch.setattr(
        "app.services.auto_analyze_service.ai_service.generate_insight",
        lambda context: ("Clustering narrative, retried successfully.", "gemini"),
    )

    auto_analyze_service.retry_ai_insights(job.id)

    db_session.refresh(job)
    assert job.result_json["ai_insight"] == "Clustering narrative, retried successfully."
    assert job.result_json["ai_available"] is True

    cache_rows = db_session.query(AIInsightCache).filter(AIInsightCache.dataset_id == job.dataset_id).all()
    assert len(cache_rows) == 1
    assert cache_rows[0].insight == "Clustering narrative, retried successfully."
    assert cache_rows[0].model_id is None


def test_retry_ai_insights_works_for_a_transaction_log_job_not_just_supervised(auth_client, monkeypatch, db_session):
    """Same gap as clustering above, for the transaction-log path — its narrative context
    (revenue/RFM/return-prediction/forecast) must actually be rebuilt and sent, not
    silently skipped just because there's no best_model_id."""
    from app.models.ai_insight_cache import AIInsightCache
    from app.services import auto_analyze_service

    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    received_context = {}

    def fake_generate(context):
        received_context.update(context)
        return "Transaction-log narrative, retried successfully.", "gemini"

    job = _make_completed_job(
        db_session,
        project_id,
        dataset_id,
        result_json={
            "analysis_type": "transaction_log",
            "best_model_id": None,
            "transaction_analysis": {
                "revenue_analytics": {"total_revenue": 50000.0},
                "customer_rfm": {"segments": {"Champions": 12}},
            },
            "ai_insight": None,
            "ai_available": False,
            "ai_reason": "rate_limited",
        },
    )

    monkeypatch.setattr("app.services.auto_analyze_service.ai_service.generate_insight", fake_generate)

    auto_analyze_service.retry_ai_insights(job.id)

    db_session.refresh(job)
    assert job.result_json["ai_insight"] == "Transaction-log narrative, retried successfully."
    assert job.result_json["ai_available"] is True
    assert received_context.get("revenue_analytics") == {"total_revenue": 50000.0}
    assert received_context.get("customer_segments") == {"Champions": 12}

    cache_rows = db_session.query(AIInsightCache).filter(AIInsightCache.dataset_id == job.dataset_id).all()
    assert len(cache_rows) == 1
    assert cache_rows[0].insight == "Transaction-log narrative, retried successfully."


def test_confirm_target_rejects_an_unknown_column(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]
    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]

    resp = auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": "not_a_real_column"})
    assert resp.status_code == 422


def test_auto_analyze_remembers_confirmed_target_on_rerun(auth_client):
    """'Remember the choice for re-runs': once a target has been confirmed for a project,
    a second Auto Analyze run on the same project skips the pause."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    first = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    first_job_id = first.json()["id"]
    auth_client.post(f"/api/auto-analyze/{first_job_id}/confirm-target", json={"target_column": "churn"})
    assert auth_client.get(f"/api/auto-analyze/{first_job_id}").json()["status"] == "completed"

    second = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    second_job_id = second.json()["id"]
    second_job = auth_client.get(f"/api/auto-analyze/{second_job_id}").json()

    assert second_job["status"] != "awaiting_target_confirmation"
    assert second_job["confirmed_target_column"] == "churn"


def test_auto_analyze_latest_endpoint_returns_most_recent_job(auth_client):
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})

    latest_resp = auth_client.get(f"/api/projects/{project_id}/auto-analyze/latest")
    assert latest_resp.status_code == 200
    assert latest_resp.json()["dataset_id"] == dataset_id


def test_auto_analyze_rejects_dataset_belonging_to_a_different_own_project(auth_client):
    """Even for the same user, a dataset must belong to the project named in the URL —
    otherwise a job could be created with a mismatched project/dataset pair."""
    project_a = create_project(auth_client, "Project A")
    project_b = create_project(auth_client, "Project B")
    upload_resp = upload_csv(auth_client, project_a, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post(f"/api/projects/{project_b}/auto-analyze", json={"dataset_id": dataset_id})
    assert resp.status_code == 404


def test_auto_analyze_rejects_dataset_from_another_project_scope(client):
    client.post(
        "/api/auth/register",
        json={"name": "Owner", "email": "owner-aa@example.com", "password": "password123"},
    )
    login = client.post("/api/auth/login", json={"email": "owner-aa@example.com", "password": "password123"})
    client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
    project_id = create_project(client, "Owner AA Project")
    upload_resp = upload_csv(client, project_id, _make_classification_csv())
    dataset_id = upload_resp.json()["id"]

    client.post(
        "/api/auth/register",
        json={"name": "Intruder", "email": "intruder-aa@example.com", "password": "password123"},
    )
    login2 = client.post("/api/auth/login", json={"email": "intruder-aa@example.com", "password": "password123"})
    client.headers.update({"Authorization": f"Bearer {login2.json()['access_token']}"})
    other_project_id = create_project(client, "Intruder AA Project")

    resp = client.post(
        f"/api/projects/{other_project_id}/auto-analyze", json={"dataset_id": dataset_id}
    )
    assert resp.status_code == 404


def test_auto_analyze_updates_project_status_through_the_run(auth_client):
    """Regression test: the project header stayed stuck on 'Draft' after Auto Analyze
    completed. Status must become 'analyzing' while the job runs and 'completed' once it
    finishes successfully."""
    project_id = create_project(auth_client)
    assert auth_client.get(f"/api/projects/{project_id}").json()["status"] == "draft"

    upload_resp = upload_csv(auth_client, project_id, _csv_with_duplicates())
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]

    # By the time TestClient's synchronous background-task execution returns control here,
    # the job has already paused for target confirmation — status must already be "analyzing",
    # not still "draft".
    assert auth_client.get(f"/api/projects/{project_id}").json()["status"] == "analyzing"

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    target = job["target_candidates_json"][0]["column"]
    auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": target})

    final_job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert final_job["status"] == "completed"
    assert auth_client.get(f"/api/projects/{project_id}").json()["status"] == "completed"


def test_auto_analyze_marks_project_failed_when_the_job_fails(auth_client):
    project_id = create_project(auth_client)

    # Fewer than 20 rows after cleaning -> train_models raises DatasetValidationError,
    # which must propagate into project.status, not leave it stuck on "analyzing".
    content = "age,income,churn\n" + "\n".join(
        f"{20 + i},{30000 + i * 100},{i % 2}" for i in range(10)
    )
    upload_resp = upload_csv(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]
    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    target = job["target_candidates_json"][0]["column"]
    auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": target})

    final_job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert final_job["status"] == "failed"
    assert auth_client.get(f"/api/projects/{project_id}").json()["status"] == "failed"


def test_auto_analyze_profile_and_cleaning_steps_report_original_row_count(auth_client):
    """Regression test: the 'Profile dataset' step reported dataset.row_count, which gets
    overwritten to the CLEANED count after cleaning runs — on a re-run over an already-
    cleaned dataset this silently showed the previous clean's row count instead of the
    file's true original size. The profile step must always reflect the untouched upload,
    and the cleaning step must show the before -> after transition explicitly."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _csv_with_duplicates(n=100, dup_count=5))
    dataset_id = upload_resp.json()["id"]

    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    job_id = start_resp.json()["id"]
    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()

    profile_step = next(s for s in job["steps_json"] if s["key"] == "profile")
    assert "105 rows" in profile_step["detail"]

    cleaning_step = next(s for s in job["steps_json"] if s["key"] == "cleaning")
    assert "105 rows" in cleaning_step["detail"]
    assert "100 rows after cleaning" in cleaning_step["detail"]

    # Re-running Auto Analyze over the now-already-cleaned dataset must still report the
    # ORIGINAL 105 rows in the profile step, not the 100 rows left over from the last clean.
    second_start = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    second_job = auth_client.get(f"/api/auto-analyze/{second_start.json()['id']}").json()
    second_profile_step = next(s for s in second_job["steps_json"] if s["key"] == "profile")
    assert "105 rows" in second_profile_step["detail"]
