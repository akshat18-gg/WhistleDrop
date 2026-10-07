import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import case_codes
from app.config import get_settings
from app.main import MAX_BODY_BYTES, app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def assert_error_shape(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


@pytest.mark.parametrize("content", [b"{bad json", b'{"category": "SECURITY",', b"\xff\xfe", b"category=SECURITY"])
def test_body_that_is_not_json_is_400(client, content):
    response = client.post("/api/reports", content=content, headers={"Content-Type": "application/json"})
    assert_error_shape(response, 400, "INVALID_JSON")


def test_body_with_non_json_content_type_is_400(client):
    response = client.post("/api/reports", content=b"hello", headers={"Content-Type": "text/plain"})
    assert_error_shape(response, 400, "INVALID_JSON")


def test_json_that_is_not_an_object_is_422(client):
    response = client.post("/api/reports", json=["SECURITY", "something happened"])
    assert_error_shape(response, 422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"] == [{"field": "body", "problem": "Must be a JSON object."}]


def test_missing_body_is_422(client):
    response = client.post("/api/reports")
    assert_error_shape(response, 422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"][0]["problem"] == "This field is required."


def test_body_too_large_is_413(client):
    huge = b'{"description": "' + b"x" * MAX_BODY_BYTES + b'"}'
    response = client.post("/api/reports", content=huge, headers={"Content-Type": "application/json"})
    assert_error_shape(response, 413, "BODY_TOO_LARGE")


def test_body_too_large_without_content_length_is_413(client):
    def chunks():
        yield b'{"description": "'
        for _ in range(10):
            yield b"x" * 8192
        yield b'"}'

    response = client.post("/api/reports", content=chunks(), headers={"Content-Type": "application/json"})
    assert "content-length" not in response.request.headers
    assert_error_shape(response, 413, "BODY_TOO_LARGE")


def test_largest_allowed_report_still_fits(submit):
    # 5000 characters of 3-byte UTF-8 is about 15 KB, well under the limit
    assert submit(description="अ" * 5000).status_code == 201


def test_validation_details_are_readable(client):
    response = client.post(
        "/api/reports",
        json={"category": "parking", "description": "short", "evidence_url": "ftp://x.com/a", "email": "a@b.c"},
    )
    assert_error_shape(response, 422, "VALIDATION_ERROR")
    details = {d["field"]: d["problem"] for d in response.json()["error"]["details"]}
    assert details == {
        "category": "Must be one of 'SECURITY', 'HARASSMENT', 'CORRUPTION', 'TECHNICAL' or 'OTHER'.",
        "description": "Must be at least 20 characters, not counting spaces at the ends.",
        "evidence_url": "Must be an http:// or https:// link, like https://drive.google.com/...",
        "email": "This field isn't accepted here.",
    }


def test_validation_errors_do_not_echo_input(client):
    response = client.post("/api/reports", json={"category": "SECURITY", "description": "my secret!!"})
    assert "my secret" not in response.text


def test_query_and_path_errors_use_the_same_shape(client, auth):
    response = client.get("/api/moderator/reports", headers=auth, params={"from": "2026-10-05", "to": "2026-10-01", "page": 0})
    assert_error_shape(response, 422, "VALIDATION_ERROR")
    fields = {d["field"] for d in response.json()["error"]["details"]}
    assert fields == {"to", "page"}

    response = client.get("/api/moderator/reports/not-a-uuid", headers=auth)
    assert_error_shape(response, 422, "VALIDATION_ERROR")
    assert response.json()["error"]["details"][0]["field"] == "report_id"


def test_unknown_route_is_404_in_our_shape(client):
    assert_error_shape(client.get("/api/nothing-here"), 404, "NOT_FOUND")
    assert_error_shape(client.get("/nothing-here"), 404, "NOT_FOUND")


def test_wrong_method_is_405_in_our_shape(client):
    response = client.delete("/api/reports")
    assert_error_shape(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "POST"
    assert_error_shape(client.post("/health"), 405, "METHOD_NOT_ALLOWED")


def test_unexpected_error_is_generic_500_and_logs_no_report_text(monkeypatch, caplog):
    def broken():
        raise RuntimeError("database fell over")

    monkeypatch.setattr(case_codes, "generate", broken)
    client = TestClient(app, raise_server_exceptions=False)
    secret_text = "The accountant in block C is taking cash for seats."
    with caplog.at_level(logging.INFO, logger="whistledrop"):
        response = client.post("/api/reports", json={"category": "CORRUPTION", "description": secret_text})

    assert_error_shape(response, 500, "INTERNAL_ERROR")
    assert "fell over" not in response.text
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "database fell over" in caplog.text
    assert "accountant" not in caplog.text


def test_running_out_of_case_code_retries_is_500(submit, monkeypatch):
    monkeypatch.setattr(case_codes, "generate", lambda: "AAAAAAAAAAAAAAAA")
    assert submit().status_code == 201
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/reports", json={"category": "OTHER", "description": "Another report that collides."})
    assert_error_shape(response, 500, "INTERNAL_ERROR")


def test_security_headers_on_every_kind_of_response(client, auth, submit):
    code = submit().json()["case_code"]
    responses = [
        client.get("/health"),
        client.get("/api/categories"),
        submit(),
        client.get("/api/reports/status", headers={"X-Case-Code": code}),
        client.get("/api/reports/status"),
        client.get("/api/moderator/reports"),
        client.get("/api/moderator/reports", headers=auth),
        client.get("/nope"),
        client.post("/api/reports", content=b"x" * (MAX_BODY_BYTES + 1)),
        client.get("/docs"),
    ]
    for response in responses:
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "set-cookie" not in response.headers


def test_reporter_endpoints_are_not_cached(client, submit):
    submitted = submit()
    code = submitted.json()["case_code"]
    for response in [
        submitted,
        client.get("/api/reports/status", headers={"X-Case-Code": code}),
        client.get("/api/reports/status", headers={"X-Case-Code": "WD-0000-0000-0000-0000"}),
        client.get("/api/categories"),
    ]:
        assert response.headers["cache-control"] == "no-store"


def test_request_log_has_route_template_only(client, auth, make_report, caplog):
    report = make_report()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="whistledrop"):
        client.get(f"/api/moderator/reports/{report.id}", headers=auth)
        client.get("/api/moderator/reports", headers=auth, params={"q": "searchterm"})
        client.post("/api/reports", json={"category": "OTHER", "description": "Body text that must stay out of logs."})
        client.get("/api/reports/WD-7K3M-Q9XA-2HFD-R8TN")

    lines = [record.getMessage() for record in caplog.records if record.name == "whistledrop"]
    assert lines[0].startswith("GET /api/moderator/reports/{report_id} 200 ")
    assert lines[1].startswith("GET /api/moderator/reports 200 ")
    assert lines[2].startswith("POST /api/reports 201 ")
    assert lines[3].startswith("GET (no matching route) 404 ")
    everything = "\n".join(lines)
    for leaked in [str(report.id), "searchterm", "must stay out", "WD-7K3M", "Bearer", "testclient"]:
        assert leaked not in everything


def test_settings_refuse_short_or_missing_secrets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env file here
    monkeypatch.setenv("JWT_SECRET", "too-short")
    monkeypatch.delenv("CASE_CODE_SECRET")
    monkeypatch.setenv("ENCRYPTION_KEY", "not-a-fernet-key")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as refused:
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    message = str(refused.value)
    assert message.startswith("Refusing to start.")
    assert "CASE_CODE_SECRET has to be at least 32 characters" in message
    assert "JWT_SECRET has to be at least 32 characters" in message
    assert "ENCRYPTION_KEY has to be a Fernet key" in message


GOOD_SECRETS = {
    "JWT_SECRET": "x" * 40,
    "CASE_CODE_SECRET": "y" * 40,
    "ENCRYPTION_KEY": "N2xvbmctZW5vdWdoLWZvci1hLWZlcm5ldC1rZXktMTI=",
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"JWT_SECRET": None, "CASE_CODE_SECRET": None, "ENCRYPTION_KEY": None},
        {"CASE_CODE_SECRET": None},
        {"CASE_CODE_SECRET": "y" * 31},
        {"ENCRYPTION_KEY": None},
        {"ENCRYPTION_KEY": "too-short-to-be-a-key"},
    ],
    ids=["none set", "one missing", "one too short", "no encryption key", "bad encryption key"],
)
def test_app_refuses_to_start_without_proper_secrets(tmp_path, overrides):
    env = {k: v for k, v in os.environ.items() if k not in GOOD_SECRETS}
    for name, value in {**GOOD_SECRETS, **overrides}.items():
        if value is not None:
            env[name] = value
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Refusing to start" in result.stderr


def test_app_starts_with_proper_secrets(tmp_path):
    env = {**os.environ, **GOOD_SECRETS, "PYTHONPATH": str(PROJECT_ROOT)}
    result = subprocess.run([sys.executable, "-c", "import app.main"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
