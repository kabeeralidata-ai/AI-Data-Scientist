import io

from app.services import eda_service


def create_project(client, name="EDA Dashboard Project"):
    resp = client.post("/api/projects", json={"name": name, "description": "test"})
    assert resp.status_code == 201
    return resp.json()["id"]


def upload_dataset(client, project_id, content, filename="data.csv"):
    resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": (filename, io.BytesIO(content.encode()), "text/csv")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def churn_like_csv(rows: int = 200) -> str:
    """A dataset shaped like a real churn/retention export: a binary outcome flag
    encoded 0/1 (the minority class), a low-cardinality categorical, a higher-
    cardinality categorical, a date column, and a couple of numeric columns —
    enough column-type variety to exercise KPI/chart auto-selection generically."""
    lines = ["signup_date,region,plan,tenure_months,monthly_spend,churn"]
    regions = ["North", "South", "East", "West", "Central"]
    plans = ["Basic", "Standard", "Premium"]
    for i in range(rows):
        month = (i % 12) + 1
        day = (i % 27) + 1
        region = regions[i % len(regions)]
        plan = plans[i % len(plans)]
        tenure = 1 + (i % 48)
        spend = 20 + (i % 10) * 7.5
        churn = 1 if i % 4 == 0 else 0
        lines.append(f"2024-{month:02d}-{day:02d},{region},{plan},{tenure},{spend},{churn}")
    return "\n".join(lines)


def query_eda(client, dataset_id, categorical_filters=None, date_filters=None):
    return client.post(
        f"/api/analysis/{dataset_id}/eda/query",
        json={
            "categorical_filters": categorical_filters or {},
            "date_filters": date_filters or {},
        },
    )


def test_filtered_eda_auto_selects_kpis_charts_and_filter_options(auth_client):
    project_id = create_project(auth_client)
    dataset_id = upload_dataset(auth_client, project_id, churn_like_csv())

    resp = query_eda(auth_client, dataset_id)
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["total_rows"] == 200
    assert body["summary"]["filtered_rows"] == 200
    assert body["summary"]["is_filtered"] is False

    kpi_labels = [k["label"] for k in body["kpis"]]
    assert "Total Rows" in kpi_labels
    # the binary churn flag's minority ("1") class must drive the rate, not the
    # majority class, so the label reads "Churn Rate" rather than "0 Churn Rate"
    assert "Churn Rate" in kpi_labels
    churn_kpi = next(k for k in body["kpis"] if k["label"] == "Churn Rate")
    assert abs(churn_kpi["value"] - 0.25) < 1e-9

    donut_columns = {d["column"] for d in body["donut_charts"]}
    bar_columns = {b["column"] for b in body["bar_charts"]}
    # region (5) and plan (3) are the only categorical columns in this dataset —
    # neither should be silently dropped by the donut/bar split
    assert donut_columns | bar_columns == {"region", "plan"}
    assert donut_columns.isdisjoint(bar_columns)

    assert body["rating_breakdown"] is None or body["rating_breakdown"]["column"] != "churn"

    assert body["time_trend"]["date_column"] == "signup_date"
    assert len(body["time_trend"]["labels"]) > 0

    assert set(body["filter_options"]["categorical"].keys()) == {"region", "plan"}
    assert body["filter_options"]["date_ranges"]["signup_date"]["min"]
    assert body["filter_options"]["date_ranges"]["signup_date"]["max"]


def test_categorical_filter_narrows_every_number_correctly(auth_client):
    project_id = create_project(auth_client)
    dataset_id = upload_dataset(auth_client, project_id, churn_like_csv())

    resp = query_eda(auth_client, dataset_id, categorical_filters={"region": ["North"]})
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["is_filtered"] is True
    # region cycles every 5 rows across 200 rows -> exactly 40 rows are "North"
    assert body["summary"]["filtered_rows"] == 40
    total_rows_kpi = next(k for k in body["kpis"] if k["label"] == "Total Rows")
    assert total_rows_kpi["value"] == 40

    # every donut/bar chart's counts must sum to the filtered row count, not the full dataset
    for chart in body["donut_charts"] + body["bar_charts"]:
        assert sum(item["count"] for item in chart["items"]) == 40


def test_date_range_filter_narrows_rows(auth_client):
    project_id = create_project(auth_client)
    dataset_id = upload_dataset(auth_client, project_id, churn_like_csv())

    full = query_eda(auth_client, dataset_id).json()
    full_range = full["filter_options"]["date_ranges"]["signup_date"]

    resp = query_eda(
        auth_client,
        dataset_id,
        date_filters={"signup_date": {"start": full_range["min"], "end": full_range["min"]}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 0 < body["summary"]["filtered_rows"] < full["summary"]["filtered_rows"]


def test_filter_options_stay_based_on_full_unfiltered_dataset(auth_client):
    project_id = create_project(auth_client)
    dataset_id = upload_dataset(auth_client, project_id, churn_like_csv())

    unfiltered = query_eda(auth_client, dataset_id).json()
    filtered = query_eda(auth_client, dataset_id, categorical_filters={"region": ["North"]}).json()

    assert filtered["filter_options"] == unfiltered["filter_options"]
    assert sorted(filtered["filter_options"]["categorical"]["region"]) == [
        "Central",
        "East",
        "North",
        "South",
        "West",
    ]


def test_empty_filter_result_returns_zeroed_kpis_not_an_error(auth_client):
    project_id = create_project(auth_client)
    dataset_id = upload_dataset(auth_client, project_id, churn_like_csv())

    resp = query_eda(auth_client, dataset_id, categorical_filters={"region": ["Nonexistent Region"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["filtered_rows"] == 0
    total_rows_kpi = next(k for k in body["kpis"] if k["label"] == "Total Rows")
    assert total_rows_kpi["value"] == 0
    assert body["donut_charts"] == [] or all(c["items"] == [] for c in body["donut_charts"])


def test_identical_filters_are_served_from_cache(auth_client, monkeypatch):
    project_id = create_project(auth_client)
    dataset_id = upload_dataset(auth_client, project_id, churn_like_csv())

    call_count = {"n": 0}
    original_load = eda_service.load_dataframe

    def counting_load(dataset):
        call_count["n"] += 1
        return original_load(dataset)

    monkeypatch.setattr(eda_service, "load_dataframe", counting_load)

    first = query_eda(auth_client, dataset_id, categorical_filters={"region": ["North"]})
    assert first.status_code == 200
    assert call_count["n"] == 1

    second = query_eda(auth_client, dataset_id, categorical_filters={"region": ["North"]})
    assert second.status_code == 200
    assert call_count["n"] == 1, "identical filter combination should be served from cache, not recomputed"
    assert second.json() == first.json()

    third = query_eda(auth_client, dataset_id, categorical_filters={"region": ["South"]})
    assert third.status_code == 200
    assert call_count["n"] == 2, "a different filter combination must not hit the cache"


def test_cannot_query_filtered_eda_for_another_users_dataset(client):
    client.post(
        "/api/auth/register",
        json={"name": "Owner", "email": "owner-eda@example.com", "password": "password123"},
    )
    owner_login = client.post(
        "/api/auth/login", json={"email": "owner-eda@example.com", "password": "password123"}
    )
    client.headers.update({"Authorization": f"Bearer {owner_login.json()['access_token']}"})
    project_id = create_project(client)
    dataset_id = upload_dataset(client, project_id, churn_like_csv())

    client.post(
        "/api/auth/register",
        json={"name": "Intruder", "email": "intruder-eda@example.com", "password": "password123"},
    )
    intruder_login = client.post(
        "/api/auth/login", json={"email": "intruder-eda@example.com", "password": "password123"}
    )
    client.headers.update({"Authorization": f"Bearer {intruder_login.json()['access_token']}"})

    resp = query_eda(client, dataset_id)
    assert resp.status_code == 404


def test_numeric_columns_generate_total_kpis_for_dataset_without_binary_flag(auth_client):
    """A dataset with no id-like/binary outcome column at all should still auto-select
    4 sensible KPIs from whatever numeric columns exist, proving the KPI selection is
    generic rather than tuned to the churn-shaped fixture used above."""
    project_id = create_project(auth_client)
    rows = ["age,income,score"]
    for i in range(50):
        rows.append(f"{20 + i % 40},{30000 + i * 250},{i % 5}")
    dataset_id = upload_dataset(auth_client, project_id, "\n".join(rows))

    resp = query_eda(auth_client, dataset_id)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["kpis"]) == 4
    assert body["kpis"][0]["label"] == "Total Rows"
