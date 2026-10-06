import os

os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-long-enough-1234"
os.environ["CASE_CODE_SECRET"] = "test-case-code-secret-long-enough-5678"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

VALID_REPORT = {
    "category": "SECURITY",
    "description": "The lab server room door has been left propped open every night this week.",
}


@pytest.fixture(autouse=True)
def engine(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite file."""
    test_engine = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "engine", test_engine)
    db.SessionLocal.configure(bind=test_engine)
    db.create_tables()
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session():
    with db.SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def submit(client):
    """Submits a report and returns the response. Keyword arguments override the valid defaults."""

    def _submit(**overrides):
        return client.post("/api/reports", json={**VALID_REPORT, **overrides})

    return _submit
