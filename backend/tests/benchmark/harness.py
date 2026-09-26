"""Benchmark harness core — runs the FULL real pipeline from scratch (upload -> auto
analyze -> confirm -> report) against each real dataset file in backend/tests/data/, and
checks dataset-specific, real-world-grounded expected outcomes (see PROGRESS.md and each
checks_*.py module for the exact list, mirroring the task spec verbatim).

Ground rules enforced by convention across every checks_*.py module (see docs/PROGRESS.md
for the rationale):
  - Never feed an answer_key_*.csv file into the pipeline itself — read it ONLY inside a
    check function, to compare against what the pipeline already produced on its own.
  - Never tune a method/threshold to make an answer-key comparison pass — a check reports
    the real number and a real pass/fail against the task's stated criterion; if the
    pipeline is wrong, the check fails, not the criterion.
  - A dataset whose file doesn't exist in backend/tests/data/ is SKIPPED, never faked.
"""

import os
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ.setdefault("DATABASE_URL", "sqlite:///./benchmark.db")
os.environ.setdefault("SECRET_KEY", "benchmark_secret_key")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("UPLOAD_DIR", "./benchmark_storage/uploads")
os.environ.setdefault("MODEL_DIR", "./benchmark_storage/models")
os.environ.setdefault("REPORT_DIR", "./benchmark_storage/reports")
# The benchmark checks the PIPELINE's own correctness (data understanding, cleaning,
# leakage exclusion, planning, modeling, reports) — it must never depend on a live
# Gemini call succeeding (rate limits, quota, network) to produce a stable pass/fail.
os.environ["AI_PROVIDER"] = "ollama"
os.environ["GEMINI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.services.auto_analyze_service as auto_analyze_service  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
_DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "benchmark_storage"))


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class DatasetBenchmark:
    dataset_file: str
    checks: list = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None


def dataset_path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def make_client() -> TestClient:
    """A fresh, isolated SQLite database per dataset run — no state (projects, customer
    IDs, etc.) leaks between datasets, and each run is reproducible from a clean slate."""
    os.makedirs(_DB_DIR, exist_ok=True)
    db_path = os.path.join(_DB_DIR, f"bench_{int(time.time() * 1_000_000)}.db")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    auto_analyze_service.SessionLocal = TestingSessionLocal

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    email = f"benchmark_{int(time.time() * 1_000_000)}@example.com"
    reg = client.post("/api/auth/register", json={"name": "Benchmark", "email": email, "password": "password123"})
    assert reg.status_code == 201, f"benchmark user registration failed: {reg.text}"
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, f"benchmark login failed: {login.text}"
    client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
    # Check modules that need a direct DB session (e.g. to load the CLEANED dataframe via
    # dataset_service.load_dataframe) must use THIS client's isolated database, never
    # app.core.database.SessionLocal directly (that's the production session factory —
    # using it here would silently query a different database than the one this client's
    # requests actually wrote to).
    client.db_session_local = TestingSessionLocal
    return client


def upload_dataset(client: TestClient, filename: str, project_name: str | None = None) -> tuple[str, str]:
    proj = client.post("/api/projects", json={"name": project_name or f"Benchmark — {filename}"})
    assert proj.status_code == 201, f"project creation failed: {proj.text}"
    project_id = proj.json()["id"]

    with open(dataset_path(filename), "rb") as f:
        upload = client.post(
            f"/api/projects/{project_id}/datasets/upload",
            files={"file": (filename, f, "text/csv")},
        )
    assert upload.status_code == 201, f"dataset upload failed for {filename}: {upload.text}"
    return project_id, upload.json()["id"]


def run_auto_analyze(client: TestClient, project_id: str, dataset_id: str, poll_attempts: int = 40, poll_delay: float = 0.5) -> dict:
    """Starts Auto Analyze and polls to a terminal-or-paused state. Returns the final job
    dict. Does NOT auto-confirm target/plan — callers decide that, since the check itself
    (e.g. 'was the right thing suggested') often needs to inspect the paused state first."""
    start = client.post(f"/api/projects/{project_id}/auto-analyze", json={"dataset_id": dataset_id})
    assert start.status_code == 201, f"auto-analyze start failed: {start.text}"
    job_id = start.json()["id"]
    return poll_job(client, job_id, poll_attempts, poll_delay)


def poll_job(client: TestClient, job_id: str, poll_attempts: int = 40, poll_delay: float = 0.5) -> dict:
    terminal = {"completed", "failed", "awaiting_target_confirmation", "awaiting_plan_confirmation"}
    job = {}
    for _ in range(poll_attempts):
        job = client.get(f"/api/auto-analyze/{job_id}").json()
        if job.get("status") in terminal:
            return job
        time.sleep(poll_delay)
    return job


def run_dataset_benchmark(module) -> DatasetBenchmark:
    path = dataset_path(module.DATASET_FILE)
    if not os.path.exists(path):
        return DatasetBenchmark(dataset_file=module.DATASET_FILE, skipped=True, skip_reason="file not found in backend/tests/data/")
    try:
        client = make_client()
        checks = module.run(client)
    except Exception as exc:  # a harness/module bug must show up as a loud failure, not silently vanish
        return DatasetBenchmark(dataset_file=module.DATASET_FILE, error=f"{type(exc).__name__}: {exc}")
    return DatasetBenchmark(dataset_file=module.DATASET_FILE, checks=checks)


def print_report(results: list[DatasetBenchmark]) -> bool:
    """Prints the pass/fail table and returns True iff every dataset that WAS run has
    every check passing (skipped datasets don't count against this — they're a data
    availability issue, reported separately, never silently treated as passing)."""
    all_ok = True
    for r in results:
        print(f"\n=== {r.dataset_file} ===")
        if r.skipped:
            print(f"  SKIPPED — {r.skip_reason}")
            continue
        if r.error:
            print(f"  ERROR — {r.error}")
            all_ok = False
            continue
        for c in r.checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"  [{status}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
            if not c.passed:
                all_ok = False

    total = sum(len(r.checks) for r in results)
    passed = sum(1 for r in results for c in r.checks if c.passed)
    ran = sum(1 for r in results if not r.skipped and not r.error)
    skipped = sum(1 for r in results if r.skipped)
    errored = sum(1 for r in results if r.error)
    print(f"\n{passed}/{total} checks passed — {ran} dataset(s) run, {skipped} skipped (missing file), {errored} errored")
    if skipped:
        all_ok = False  # an incomplete benchmark is not a passing benchmark
    return all_ok
