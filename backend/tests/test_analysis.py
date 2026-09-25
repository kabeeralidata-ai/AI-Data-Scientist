import io


def upload_dataset(client, project_id, content):
    return client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )


def test_eda_returns_summary_and_correlations(auth_client):
    project_resp = auth_client.post("/api/projects", json={"name": "EDA Project"})
    project_id = project_resp.json()["id"]

    rows = ["age,income,score"]
    for i in range(30):
        rows.append(f"{20 + i},{30000 + i * 500},{i % 5}")
    content = "\n".join(rows)

    upload_resp = upload_dataset(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post(f"/api/analysis/{dataset_id}/eda")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["rows"] == 30
    assert "age" in body["descriptive_stats"]
    assert body["correlation"]["columns"]


def test_eda_does_not_misclassify_numeric_duration_columns_as_dates(auth_client):
    """Regression test: a numeric column whose NAME contains 'time' (e.g. a duration like
    'prep_time_min') must not be treated as a date just because pd.to_datetime happens to
    'succeed' by reinterpreting the numbers as epoch-nanosecond timestamps. Before the
    fix, this made the column appear in both numeric_cols and the name-hint datetime_cols,
    so selecting df[[dc, nc]] with dc == nc built a duplicate-column DataFrame and crashed
    set_index() with 'Data must be 1-dimensional'."""
    project_resp = auth_client.post("/api/projects", json={"name": "Delivery Time EDA Project"})
    project_id = project_resp.json()["id"]

    rows = ["order_time,prep_time_min,delivery_time_min,order_value"]
    for i in range(60):
        month = (i % 12) + 1
        day = (i % 27) + 1
        rows.append(f"2025-{month:02d}-{day:02d} 10:{i % 60:02d},{10 + i % 20},{20 + i % 30},{500 + i}")
    content = "\n".join(rows)

    upload_resp = upload_dataset(auth_client, project_id, content)
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post(f"/api/analysis/{dataset_id}/eda")
    assert resp.status_code == 200
    body = resp.json()
    # prep_time_min / delivery_time_min stay real numeric columns...
    assert "prep_time_min" in body["descriptive_stats"]
    assert "delivery_time_min" in body["descriptive_stats"]
    # ...and only the genuine date column drives the time-trend charts.
    assert all(key.endswith("_by_order_time") for key in body["time_trends"])
