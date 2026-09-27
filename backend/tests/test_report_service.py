"""Tests for the rebuilt report generation pipeline: report_context_service (the
verified, structured data every report is built from), report_service (PDF + HTML
rendering, AI insight integration), and the reports API (generation, download, preview).
All tests go through the HTTP API (like the rest of this suite) so they exercise the
same get_db-overridden test database as everything else, rather than opening a second,
unconfigured connection directly.
"""

import io
import random
import re


def create_project(client, name="Report Project", description=None):
    resp = client.post("/api/projects", json={"name": name, "description": description})
    assert resp.status_code == 201
    return resp.json()["id"]


def upload_csv(client, project_id, content, filename="data.csv"):
    resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": (filename, io.BytesIO(content.encode()), "text/csv")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _retail_csv(n=300, with_quality_issues=True):
    random.seed(9)
    cities = ["Karachi", "Lahore", "Islamabad", "Faisalabad", "Multan"]
    categories = ["Electronics", "Clothing", "Groceries"]
    rows = ["order_id,order_date,store_city,product_category,unit_price,quantity,sales_amount,profit"]
    for i in range(n):
        unit_price = round(random.uniform(5, 200), 2)
        qty = random.randint(1, 5)
        sales_amount = round(unit_price * qty, 2)
        profit = round(max(0.5, sales_amount * random.uniform(0.1, 0.4) + random.uniform(-8, 8)), 2)
        city = "" if (with_quality_issues and i % 47 == 0) else random.choice(cities)
        rows.append(
            f"ORD-{i},2024-{(i % 12) + 1:02d}-{(i % 27) + 1:02d},{city},"
            f"{random.choice(categories)},{unit_price},{qty},{sales_amount},{profit}"
        )
    if with_quality_issues:
        rows.append(rows[-1])
        rows.append(rows[-2])
    return "\n".join(rows)


def _train_model(client, dataset_id, target="sales_amount", models=None):
    resp = client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": target, "models": models or ["random_forest"]},
    )
    assert resp.status_code == 201
    return resp.json()[0]["id"]


def _generate_report(client, project_id, dataset_id=None, model_id=None, monkeypatch=None, mocked_ai=True):
    if monkeypatch and mocked_ai:
        monkeypatch.setattr(
            "app.services.report_service.ai_service.generate_report_insight",
            lambda context: ("## Key Business Insights\n- A mocked, grounded insight.", "gemini"),
        )
    payload = {}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    if model_id:
        payload["model_id"] = model_id
    resp = client.post(f"/api/reports/projects/{project_id}/generate", json=payload)
    assert resp.status_code == 201
    return resp.json()


def _preview_html(client, report_id):
    resp = client.get(f"/api/reports/{report_id}/preview")
    assert resp.status_code == 200
    return resp.text


def test_report_generation_excludes_leaked_profit_and_explains_it(auth_client, monkeypatch):
    """End-to-end version of the reported bug: generate a real report for a retail
    regression target and confirm profit is excluded and the report explains why,
    rather than presenting an R² inflated by leakage."""
    project_id = create_project(auth_client, description="Retail sales with unit price, profit.")
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)

    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    assert report["content_json"]["ai_available"] is True

    html = _preview_html(auth_client, report["id"])
    assert "Leakage" in html
    assert "Profit" in html
    # profit must not appear in "Features used" — extract that section specifically
    features_section = html.split("Features used")[1].split("Features excluded")[0]
    assert "Profit" not in features_section


def test_data_quality_shows_original_and_cleaned_figures_distinctly(auth_client, monkeypatch):
    """Priority 3: the report must show ORIGINAL (as-uploaded) data quality figures even
    after the dataset has been cleaned to zero — never silently replaced."""
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())

    quality_resp = auth_client.get(f"/api/datasets/{dataset_id}/quality")
    original_issues = quality_resp.json()
    assert any(i["type"] == "missing_values" for i in original_issues)

    clean_resp = auth_client.post(
        f"/api/analysis/{dataset_id}/clean", json={"missing_strategy": "auto", "remove_duplicates": True}
    )
    assert clean_resp.status_code == 200
    assert clean_resp.json()["missing_values"] == 0
    assert clean_resp.json()["duplicate_rows"] == 0

    report = _generate_report(auth_client, project_id, dataset_id, None, monkeypatch)
    html = _preview_html(auth_client, report["id"])

    quality_section = html.split("Data Quality")[1].split("Key EDA Findings")[0]
    assert "Cleaning performed" in quality_section
    assert re.search(r"After cleaning:</strong>\s*0 missing cell\(s\),\s*0 duplicate row\(s\)", quality_section)
    # the ORIGINAL (pre-cleaning) counts must still be visible, not overwritten by zero
    assert re.search(r"Missing cells</div><div class=\"kpi-value\">[1-9]", quality_section)
    assert re.search(r"Duplicate rows</div><div class=\"kpi-value\">[1-9]", quality_section)


def test_report_html_contains_no_raw_python_dumps(auth_client, monkeypatch):
    """Priority 4: no raw dict/list Python representations anywhere in the rendered
    report — every backend structure must be converted to tables/charts/sentences."""
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])

    assert not re.search(r"\{'[a-zA-Z_]+':\s*", html)
    assert not re.search(r"\[\{'[a-zA-Z_]+':", html)
    assert "feature_columns_json" not in html
    assert "metrics_json" not in html
    assert "leakage_warnings" not in html


def test_feature_names_are_humanized_not_raw_encoded_names(auth_client, monkeypatch):
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])

    assert "product_category_Electronics" not in html
    assert "store_city_" not in html


def test_pdf_file_is_written_and_non_trivial_size(auth_client, monkeypatch):
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)

    download = auth_client.get(f"/api/reports/{report['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert len(download.content) > 5000  # a real multi-page PDF, not an empty/error stub
    assert download.content[:4] == b"%PDF"


def test_html_preview_endpoint_returns_the_same_rendered_report(auth_client, monkeypatch):
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)

    preview = auth_client.get(f"/api/reports/{report['id']}/preview")
    assert preview.status_code == 200
    assert "text/html" in preview.headers["content-type"]
    assert "<table" in preview.text
    assert "Sales Amount" in preview.text
    assert "Table of Contents" in preview.text

    # requesting it again returns byte-identical HTML — same stored source, not re-derived
    preview_again = auth_client.get(f"/api/reports/{report['id']}/preview")
    assert preview_again.text == preview.text


def test_ai_insights_unavailable_message_when_ai_service_raises(auth_client, monkeypatch):
    """AI failures must not break report generation — a clean 'unavailable' message,
    never a fabricated insight."""
    from app.services.ai_service import AIUnavailableError

    def raise_unavailable(context):
        raise AIUnavailableError("gemini: rate limited; ollama: not running")

    monkeypatch.setattr("app.services.report_service.ai_service.generate_report_insight", raise_unavailable)

    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)

    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch=None)
    assert report["content_json"]["ai_available"] is False
    assert report["content_json"]["ai_insight"] is None

    html = _preview_html(auth_client, report["id"])
    assert "AI insights are unavailable" in html
    # Regression test (previous review finding): the "AI-generated" badge must NEVER
    # appear next to the section heading when AI insights actually failed.
    ai_section = html.split("AI Business Insights")[1].split("</h1>")[0]
    assert "AI-generated" not in ai_section


def test_ai_badge_shown_only_when_ai_insights_actually_succeeded(auth_client, monkeypatch):
    """The flip side of the badge regression test above — the badge SHOULD appear right
    next to the heading when AI insights genuinely succeeded."""
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)  # mocked_ai=True by default
    assert report["content_json"]["ai_available"] is True

    html = _preview_html(auth_client, report["id"])
    ai_section = html.split("AI Business Insights")[1].split("</h1>")[0]
    assert "AI-generated" in ai_section


def test_business_question_is_always_present_even_without_a_project_description(auth_client, monkeypatch):
    """Regression test (previous review finding): a report with no project description
    must still state SOME business question, not omit the line entirely."""
    project_id = create_project(auth_client, description=None)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])
    assert "Business question:" in html


def test_business_question_uses_the_project_description_when_present(auth_client, monkeypatch):
    project_id = create_project(auth_client, description="Which unit price and quantity combinations drive the most sales?")
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id)
    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])
    assert "Which unit price and quantity combinations drive the most sales?" in html


def test_recommendations_fall_back_to_ai_narrative_when_there_is_no_trained_model(auth_client, monkeypatch):
    """Regression test (previous review finding): 'Top recommendations' must not say 'No
    verified model-driven recommendations are available' when the AI narrative actually
    has real recommendations to show. future_outlook (the verified-data recommendation
    source) only exists when a model was trained — a dataset-only report is exactly the
    case that used to fall through to the generic 'none available' message even though
    the AI narrative's own '## Recommendations' bullets were sitting right there unused."""
    monkeypatch.setattr(
        "app.services.report_service.ai_service.generate_report_insight",
        lambda context: (
            "## Key Business Insights\n- Sales are concentrated in Electronics.\n"
            "## Recommendations\n- Increase Electronics inventory.\n- Review Clothing pricing.\n"
            "## Areas to Investigate\n- Regional variance.",
            "gemini",
        ),
    )
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    report = _generate_report(auth_client, project_id, dataset_id, None, monkeypatch=None)
    html = _preview_html(auth_client, report["id"])

    recommendations_section = html.split("Top recommendations")[1].split("</ul>")[0]
    assert "Increase Electronics inventory" in recommendations_section
    assert "Review Clothing pricing" in recommendations_section
    assert "No verified model-driven recommendations" not in recommendations_section


def test_report_generation_with_no_dataset_or_model_does_not_crash(auth_client):
    """A brand-new project with nothing uploaded yet must still produce a valid report
    that explains what's missing, rather than erroring."""
    project_id = create_project(auth_client, name="Empty Project")
    resp = auth_client.post(f"/api/reports/projects/{project_id}/generate", json={})
    assert resp.status_code == 201
    html = _preview_html(auth_client, resp.json()["id"])
    assert "<html" in html.lower()


def _churn_csv(n=200, seed=3):
    random.seed(seed)
    rows = ["customer_id,age,region,tenure_months,churn,signup_date"]
    for i in range(n):
        churn = 1 if (i % 3 == 0) else 0
        rows.append(
            f"CUST-{i},{20 + i % 50},{'North' if i % 2 else 'South'},{1 + i % 48},{churn},2024-{(i%12)+1:02d}-01"
        )
    return "\n".join(rows)


def test_future_outlook_churn_with_customer_id_discusses_customer_groups(auth_client, monkeypatch):
    project_id = create_project(auth_client, description="Predict customer churn.")
    dataset_id = upload_csv(auth_client, project_id, _churn_csv())
    model_id = _train_model(auth_client, dataset_id, target="churn", models=["logistic_regression"])

    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])
    outlook_section = html.split("Future Outlook")[1].split("AI Business Insights")[0]
    assert "customer" in outlook_section.lower()
    assert "cannot determine" not in outlook_section.lower()


def _churn_named_but_no_customer_id_csv(n=200, seed=5):
    """A retail-style dataset with an ORDER id (not a customer id) and a target NAMED
    'churn' anyway, to test the no-customer-id disclaimer fires correctly."""
    random.seed(seed)
    rows = ["order_id,category,amount,churn"]
    for i in range(n):
        rows.append(f"ORD-{i},cat{i % 3},{round(random.uniform(10, 500), 2)},{i % 2}")
    return "\n".join(rows)


def test_future_outlook_churn_target_without_customer_id_shows_disclaimer(auth_client, monkeypatch):
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _churn_named_but_no_customer_id_csv())
    model_id = _train_model(auth_client, dataset_id, target="churn", models=["logistic_regression"])

    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])
    outlook_section = html.split("Future Outlook")[1].split("AI Business Insights")[0]
    assert "cannot determine" in outlook_section.lower()


def test_future_outlook_regression_discusses_predictors_not_customer_groups(auth_client, monkeypatch):
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id, target="sales_amount")

    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])
    outlook_section = html.split("Future Outlook")[1].split("AI Business Insights")[0]
    assert "predictor" in outlook_section.lower()
    assert "Group size" not in outlook_section  # no customer/record grouping table for regression


def test_retail_returns_target_does_not_discuss_customer_retention(auth_client, monkeypatch):
    """Dataset-specific verification: a retail 'returned' classification target must be
    framed as product/order returns, never as customer retention/churn."""
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id, target="product_category", models=["logistic_regression"])

    report = _generate_report(auth_client, project_id, dataset_id, model_id, monkeypatch)
    html = _preview_html(auth_client, report["id"])
    outlook_section = html.split("Future Outlook")[1].split("AI Business Insights")[0]
    assert "customer retention" not in outlook_section.lower()
    assert "churn" not in outlook_section.lower()


def test_late_delivery_style_categorical_leak_against_numeric_target_is_caught():
    """The Karachi-delivery-shaped bug found during live verification: a binary
    categorical feature ('late_delivery': Yes/No) structurally derived from a NUMERIC
    regression target used to be invisible to every leakage check, since correlation
    against a numeric target only ever coerced the feature with pd.to_numeric (which
    fails for 'Yes'/'No', silently skipping it). It must now be caught via the
    post-outcome name-hint ('late') + factorized point-biserial correlation."""
    import pandas as pd

    from app.services.ml_service import resolve_training_features

    random.seed(21)
    n = 400
    rows = []
    for _ in range(n):
        distance = random.uniform(1, 15)
        promised = round(30 + distance * 3 + random.uniform(-5, 5), 1)
        delivery = promised + random.uniform(-25, 25)
        late = "Yes" if delivery > promised else "No"
        rows.append(
            {
                "distance_km": distance,
                "promised_time_min": promised,
                "items_count": random.randint(1, 8),
                "prep_time_min": random.uniform(5, 40),
                "delivery_time_min": delivery,
                "late_delivery": late,
            }
        )
    df = pd.DataFrame(rows)

    _, resolution = resolve_training_features(df, "delivery_time_min", None)
    assert "late_delivery" not in resolution.features
    assert "distance_km" in resolution.features
    flagged_leakage = {w["column"] for w in resolution.leakage_warnings}
    flagged_post_outcome = {w["column"] for w in resolution.post_outcome_excluded}
    assert "late_delivery" in flagged_leakage or "late_delivery" in flagged_post_outcome


# ── Phase 9 Finding A: manual report regeneration must use completed Auto Analyze context ──────

def _numeric_cluster_csv(n=180):
    """A dataset with several numeric behavioral columns and NO detectable target —
    designed to produce a real clustering result when Auto Analyze runs."""
    random.seed(77)
    rows = ["sensor_id,usage_a,usage_b,usage_c,usage_d"]
    for i in range(n):
        a = round(random.uniform(0, 100), 2)
        b = round(a * 0.4 + random.uniform(-15, 15), 2)
        c = round(random.uniform(-30, 80), 2)
        d = round(random.uniform(5, 50), 2)
        rows.append(f"SID-{i},{a},{b},{c},{d}")
    return "\n".join(rows)


def _transaction_log_csv(n=200):
    """Minimal transaction log: one date, one customer ID, one numeric amount.
    Designed to be classified as transaction_log by data_understanding_service
    (multiple events per customer_id, a clear timestamp column)."""
    random.seed(88)
    rows = ["transaction_date,customer_id,item_category,amount_pkr,quantity"]
    customers = [f"CUST-{i}" for i in range(20)]
    categories = ["Food", "Drink", "Snack"]
    for i in range(n):
        d = f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        cust = customers[i % len(customers)]
        amt = round(random.uniform(100, 2000), 2)
        qty = random.randint(1, 5)
        rows.append(f"{d},{cust},{random.choice(categories)},{amt},{qty}")
    return "\n".join(rows)


def test_manual_report_regeneration_uses_clustering_context(auth_client, monkeypatch):
    """Phase 9 Finding A — regression test.

    Root cause: POST /api/reports/projects/{id}/generate previously NEVER passed
    `clusters` or `transaction_analysis` into report_service.generate_report(), so
    re-generating a report for a clustering project (model=None, no MLModel row)
    produced a generic 'no model detected' placeholder instead of the real k-means result
    that the Auto Analyze pipeline had already computed and stored on the job.

    This test reproduces that exact condition (project has a completed clustering job, user
    manually calls the report-generate endpoint) and verifies the regenerated report
    contains real cluster/segment content — NOT a fallback placeholder."""
    monkeypatch.setattr("app.services.auto_analyze_service.dataset_service.score_target_candidates", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.services.report_service.ai_service.generate_report_insight",
        lambda context: ("## Insights\n- Mocked insight.", "gemini"),
    )

    project_id = create_project(auth_client, "Phase9-Regression-Clustering")
    dataset_id = upload_csv(auth_client, project_id, _numeric_cluster_csv())

    # Run Auto Analyze through to clustering completion
    start_resp = auth_client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start_resp.status_code == 201
    job_id = start_resp.json()["id"]

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "awaiting_plan_confirmation"

    confirm_resp = auth_client.post(f"/api/auto-analyze/{job_id}/confirm-plan")
    assert confirm_resp.status_code == 200

    job = auth_client.get(f"/api/auto-analyze/{job_id}").json()
    assert job["status"] == "completed"
    assert job["result_json"]["analysis_type"] == "clustering"
    assert job["result_json"]["clusters"] is not None

    # Now manually regenerate a NEW report through the report-generate endpoint
    # (the same path the UI uses when the user presses "Generate Report" in the Reports tab)
    regen_resp = auth_client.post(f"/api/reports/projects/{project_id}/generate", json={})
    assert regen_resp.status_code == 201
    regen_report_id = regen_resp.json()["id"]

    # The REGENERATED report must contain real cluster/segment content — not a placeholder
    preview = auth_client.get(f"/api/reports/{regen_report_id}/preview")
    assert preview.status_code == 200
    html = preview.text

    assert "Cluster Analysis" in html, (
        "Regenerated report missing 'Cluster Analysis' section — the fix to pass clusters= "
        "from the completed Auto Analyze job to generate_report() is not working."
    )
    assert "Segment" in html, "Regenerated report missing Segment content"
    # Must NOT be the old generic placeholder text
    assert "No model was trained for this dataset" not in html or "No target column was confidently detected" in html


def test_manual_report_regeneration_does_not_use_clustering_when_model_exists(auth_client, monkeypatch):
    """Regression guard: for supervised projects with a real MLModel, manual report
    regeneration must still use that model — not try to load phantom clusters from a
    non-existent Auto Analyze job."""
    monkeypatch.setattr(
        "app.services.report_service.ai_service.generate_report_insight",
        lambda context: ("## Insights\n- Mocked.", "gemini"),
    )

    project_id = create_project(auth_client, "Phase9-Regression-Supervised")
    dataset_id = upload_csv(auth_client, project_id, _retail_csv())
    model_id = _train_model(auth_client, dataset_id, target="sales_amount")

    regen_resp = auth_client.post(f"/api/reports/projects/{project_id}/generate", json={})
    assert regen_resp.status_code == 201
    html = _preview_html(auth_client, regen_resp.json()["id"])

    # Supervised report sections are present
    assert "Sales Amount" in html or "sales_amount" in html.lower()
    # No cluster analysis section for a supervised project
    assert "Cluster Analysis" not in html
