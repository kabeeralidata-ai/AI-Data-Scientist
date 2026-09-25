"""Tests for the target-reselection fix: a remembered target must not be blindly reused
forever — only when it's backed by at least one non-weak model, and never when the user
explicitly asks to change it ("Change target")."""

import io
import random


def create_project(client, name="Target Reselection Project"):
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def upload_csv(client, project_id, content):
    return client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )


def _mixed_target_csv(n=900):
    """store_city is a garbage 5-way grouping column with no real relationship to
    anything else (the exact original bug); churn is a genuinely learnable binary
    target from the same numeric features. n is large enough that every candidate
    model's ROC-AUC on store_city reliably lands within the "no real signal" band —
    a smaller n leaves room for one model (out of several tried) to randomly land just
    outside it by chance alone, a classic best-of-several-random-draws selection effect."""
    random.seed(2)
    cities = ["Karachi", "Lahore", "Islamabad", "Faisalabad", "Multan"]
    rows = ["unit_price,quantity,store_city,churn"]
    for _ in range(n):
        price = round(random.uniform(5, 200), 2)
        qty = random.randint(1, 10)
        city = random.choice(cities)
        churn = 1 if price < 50 and qty < 5 else 0
        rows.append(f"{price},{qty},{city},{churn}")
    return "\n".join(rows)


def _run_and_confirm(auth_client, project_id, dataset_id, target, force=False):
    start = auth_client.post(
        f"/api/projects/{project_id}/auto-analyze",
        json={"dataset_id": dataset_id, "force_target_reselection": force},
    )
    job_id = start.json()["id"]
    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    if job["status"] == "awaiting_target_confirmation":
        auth_client.post(f"/api/auto-analyze/{job_id}/confirm-target", json={"target_column": target})
        job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    return job


def test_weak_remembered_target_is_not_silently_reused(auth_client):
    """Regression test for the exact reported bug: confirming a garbage grouping column
    once must not make Auto Analyze reuse it forever — since the only model trained for
    it is weak, the next run must pause for confirmation again instead of silently
    repeating 'Using previously confirmed target: store_city'."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _mixed_target_csv())
    dataset_id = upload_resp.json()["id"]

    first = _run_and_confirm(auth_client, project_id, dataset_id, "store_city")
    assert first["status"] == "completed"
    assert first["confirmed_target_column"] == "store_city"
    assert first["result_json"]["is_weak_model"] is True

    second_start = auth_client.post(
        f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id}
    )
    second_job_id = second_start.json()["id"]
    second_job = auth_client.get(f"/api/auto-analyze/{second_job_id}").json()

    assert second_job["status"] == "awaiting_target_confirmation"
    target_step = next(s for s in second_job["steps_json"] if s["key"] == "target_detection")
    assert "Using previously confirmed target" not in (target_step["detail"] or "")


def test_strong_remembered_target_is_reused_without_reprompting(auth_client):
    """Positive control: a target backed by a genuinely strong model IS trusted and
    reused silently, so normal re-runs don't force the user through confirmation every
    single time."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _mixed_target_csv())
    dataset_id = upload_resp.json()["id"]

    first = _run_and_confirm(auth_client, project_id, dataset_id, "churn")
    assert first["status"] == "completed"
    assert first["result_json"]["is_weak_model"] is False

    second_start = auth_client.post(
        f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id}
    )
    second_job_id = second_start.json()["id"]
    second_job = auth_client.get(f"/api/auto-analyze/{second_job_id}").json()

    assert second_job["status"] == "completed"
    assert second_job["confirmed_target_column"] == "churn"
    target_step = next(s for s in second_job["steps_json"] if s["key"] == "target_detection")
    assert "Using previously confirmed target: 'churn'" in target_step["detail"]


def test_change_target_forces_reselection_despite_strong_remembered_target(auth_client):
    """The explicit 'Change target' flow (force_target_reselection=true) always
    re-prompts, even when the remembered target is backed by a strong model."""
    project_id = create_project(auth_client)
    upload_resp = upload_csv(auth_client, project_id, _mixed_target_csv())
    dataset_id = upload_resp.json()["id"]

    first = _run_and_confirm(auth_client, project_id, dataset_id, "churn")
    assert first["status"] == "completed"
    assert first["result_json"]["is_weak_model"] is False

    forced_start = auth_client.post(
        f"/api/projects/{project_id}/auto-analyze",
        json={"dataset_id": dataset_id, "force_target_reselection": True},
    )
    forced_job_id = forced_start.json()["id"]
    forced_job = auth_client.get(f"/api/auto-analyze/{forced_job_id}").json()

    assert forced_job["status"] == "awaiting_target_confirmation"
    assert len(forced_job["target_candidates_json"]) > 0
