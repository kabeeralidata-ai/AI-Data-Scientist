import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("SECRET_KEY", "test_secret_key")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("UPLOAD_DIR", "./test_storage/uploads")
os.environ.setdefault("MODEL_DIR", "./test_storage/models")
os.environ.setdefault("REPORT_DIR", "./test_storage/reports")

# Force-override (not setdefault) — the test suite must always exercise the deterministic
# Ollama-unreachable degradation path, never real network calls to a live AI provider,
# regardless of what a real backend/.env happens to have configured for local dev (e.g.
# AI_PROVIDER=gemini with a real key). Real-provider behavior is covered separately via
# monkeypatched httpx calls in test_gemini_service.py — never by actually calling out.
os.environ["AI_PROVIDER"] = "ollama"
os.environ["GEMINI_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
import app.services.auto_analyze_service as auto_analyze_service

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Auto Analyze runs as a FastAPI BackgroundTask with its own DB session (it must outlive
# the request), so it can't go through the get_db dependency override above. Point it at
# the same in-memory test database the rest of the test suite uses.
auto_analyze_service.SessionLocal = TestingSessionLocal


@pytest.fixture(scope="function", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def auth_client(client):
    client.post(
        "/api/auth/register",
        json={"name": "Test User", "email": "test@example.com", "password": "password123"},
    )
    resp = client.post(
        "/api/auth/login", json={"email": "test@example.com", "password": "password123"}
    )
    token = resp.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
