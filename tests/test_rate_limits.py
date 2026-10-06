import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import client_fingerprint, limiter
from conftest import MOD_PASSWORD, VALID_REPORT


@pytest.fixture
def rate_limits():
    limiter.enabled = True
    limiter.reset()


def client_from(ip):
    return TestClient(app, client=(ip, 50000))


def test_eleventh_report_in_an_hour_is_429(rate_limits):
    client = client_from("203.0.113.7")
    for _ in range(10):
        assert client.post("/api/reports", json=VALID_REPORT).status_code == 201
    response = client.post("/api/reports", json=VALID_REPORT)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_limits_are_per_client(rate_limits):
    first, second = client_from("203.0.113.7"), client_from("198.51.100.20")
    for _ in range(10):
        first.post("/api/reports", json=VALID_REPORT)
    assert first.post("/api/reports", json=VALID_REPORT).status_code == 429
    assert second.post("/api/reports", json=VALID_REPORT).status_code == 201


def test_status_checks_are_limited_even_when_they_fail(rate_limits):
    client = client_from("203.0.113.7")
    for _ in range(30):
        response = client.get("/api/reports/status", headers={"X-Case-Code": "WD-0000-0000-0000-0000"})
        assert response.status_code == 404
    response = client.get("/api/reports/status", headers={"X-Case-Code": "WD-0000-0000-0000-0000"})
    assert response.status_code == 429


def test_login_attempts_are_limited(rate_limits, moderator):
    client = client_from("203.0.113.7")
    for _ in range(10):
        response = client.post("/api/auth/login", json={"username": "mod", "password": "wrong-password"})
        assert response.status_code == 401
    response = client.post("/api/auth/login", json={"username": "mod", "password": MOD_PASSWORD})
    assert response.status_code == 429


def test_ip_never_reaches_the_logs(rate_limits, caplog):
    client = client_from("203.0.113.7")
    with caplog.at_level(logging.DEBUG):
        for _ in range(11):
            client.post("/api/reports", json=VALID_REPORT)
    assert "203.0.113.7" not in caplog.text
    assert "POST /api/reports 429" in caplog.text
    assert not [record for record in caplog.records if record.name == "slowapi" and record.levelno < logging.ERROR]


def test_limiter_key_is_not_the_ip():
    class FakeRequest:
        class client:
            host = "203.0.113.7"

    key = client_fingerprint(FakeRequest())
    assert "203.0.113.7" not in key
    assert len(key) == 64
    assert key == client_fingerprint(FakeRequest())
