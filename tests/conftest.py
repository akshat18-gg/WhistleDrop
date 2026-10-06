import os

os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-long-enough-1234"
os.environ["CASE_CODE_SECRET"] = "test-case-code-secret-long-enough-5678"
os.environ["ENCRYPTION_KEY"] = "N2xvbmctZW5vdWdoLWZvci1hLWZlcm5ldC1rZXktMTI="

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app import case_codes, db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Moderator, Report  # noqa: E402
from app.security import hash_password, limiter  # noqa: E402

MOD_PASSWORD = "correct-horse-battery"

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


@pytest.fixture(autouse=True)
def no_rate_limits():
    """Lots of tests submit more than 10 reports. The rate limit tests switch it back on."""
    limiter.enabled = False
    limiter.reset()
    yield
    limiter.enabled = False


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


@pytest.fixture
def make_report(submit, session):
    """Submits a report through the API, then sets fields directly for tests that need a certain state."""

    def _make(category="SECURITY", description=VALID_REPORT["description"], **fields):
        code = submit(category=category, description=description).json()["case_code"]
        code_hash = case_codes.hash_code(case_codes.normalise(code))
        report = session.scalar(select(Report).where(Report.case_code_hash == code_hash))
        for name, value in fields.items():
            setattr(report, name, value)
        session.commit()
        return report

    return _make


@pytest.fixture
def make_moderator(session):
    def _make(username="mod", password=MOD_PASSWORD, is_active=True):
        moderator = Moderator(username=username, password_hash=hash_password(password), is_active=is_active)
        session.add(moderator)
        session.commit()
        return moderator

    return _make


@pytest.fixture
def moderator(make_moderator):
    return make_moderator()


@pytest.fixture
def auth(client, moderator):
    """Authorization header for a logged-in moderator."""
    response = client.post("/api/auth/login", json={"username": "mod", "password": MOD_PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
