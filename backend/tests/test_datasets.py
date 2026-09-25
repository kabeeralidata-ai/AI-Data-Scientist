import io


def create_project(client, name="My Project"):
    resp = client.post("/api/projects", json={"name": name, "description": "test"})
    assert resp.status_code == 201
    return resp.json()["id"]


def csv_file(content: str, filename: str = "data.csv"):
    return {"file": (filename, io.BytesIO(content.encode()), "text/csv")}


def test_upload_valid_csv_creates_dataset(auth_client):
    project_id = create_project(auth_client)
    content = "age,income,churn\n25,50000,0\n40,60000,1\n35,55000,0\n"
    resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload", files=csv_file(content)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["row_count"] == 3
    assert body["column_count"] == 3


def test_upload_rejects_unsupported_extension(auth_client):
    project_id = create_project(auth_client)
    resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 422


def test_upload_rejects_empty_dataframe(auth_client):
    project_id = create_project(auth_client)
    resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload", files=csv_file("col1,col2\n")
    )
    assert resp.status_code == 422


def test_user_cannot_access_other_users_project(client):
    client.post(
        "/api/auth/register",
        json={"name": "Owner", "email": "owner@example.com", "password": "password123"},
    )
    owner_login = client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "password123"}
    )
    owner_token = owner_login.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {owner_token}"})
    project_id = create_project(client, "Owner Project")

    client.post(
        "/api/auth/register",
        json={"name": "Intruder", "email": "intruder@example.com", "password": "password123"},
    )
    intruder_login = client.post(
        "/api/auth/login", json={"email": "intruder@example.com", "password": "password123"}
    )
    intruder_token = intruder_login.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {intruder_token}"})

    resp = client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 404


def test_clean_dataset_removes_duplicates_and_fills_missing(auth_client):
    project_id = create_project(auth_client)
    content = "age,income\n25,50000\n25,50000\n,60000\n35,\n"
    upload_resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload", files=csv_file(content)
    )
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post(
        f"/api/analysis/{dataset_id}/clean",
        json={"missing_strategy": "mean", "remove_duplicates": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_cleaned"] is True
    assert body["duplicate_rows"] == 0
    assert body["missing_values"] == 0
