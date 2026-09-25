def test_register_creates_user_and_returns_token(client):
    resp = client.post(
        "/api/auth/register",
        json={"name": "Alice", "email": "alice@example.com", "password": "strongpass1"},
    )
    assert resp.status_code == 201
    assert "access_token" in resp.json()


def test_register_duplicate_email_fails(client):
    payload = {"name": "Alice", "email": "alice@example.com", "password": "strongpass1"}
    client.post("/api/auth/register", json=payload)
    resp = client.post("/api/auth/register", json=payload)
    assert resp.status_code == 400


def test_login_with_wrong_password_fails(client):
    client.post(
        "/api/auth/register",
        json={"name": "Bob", "email": "bob@example.com", "password": "strongpass1"},
    )
    resp = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_me_requires_authentication(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"
