import re

import pytest

from app.models import Category

CODE_PATTERN = re.compile(r"^WD-[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")


def test_valid_report_returns_201_and_case_code(submit):
    response = submit()
    assert response.status_code == 201
    body = response.json()
    assert CODE_PATTERN.match(body["case_code"])
    assert body["status"] == "SUBMITTED"
    assert "can't be recovered" in body["note"]
    assert "only you would know" in body["privacy_tip"]


@pytest.mark.parametrize("category", [c.value for c in Category])
def test_every_category_is_accepted(submit, category):
    assert submit(category=category).status_code == 201


def test_category_is_case_insensitive_and_stored_uppercase(submit, client):
    code = submit(category="harassment").json()["case_code"]
    status = client.get("/api/reports/status", headers={"X-Case-Code": code}).json()
    assert status["category"] == "HARASSMENT"


def test_categories_endpoint_lists_all_five(client):
    response = client.get("/api/categories")
    assert response.status_code == 200
    assert [c["value"] for c in response.json()] == [
        "SECURITY",
        "HARASSMENT",
        "CORRUPTION",
        "TECHNICAL",
        "OTHER",
    ]
    assert response.json()[0]["label"] == "Security"


@pytest.mark.parametrize(
    "body",
    [
        {"description": "A long enough description of what happened here."},
        {"category": "PARKING", "description": "A long enough description of what happened here."},
        {"category": "", "description": "A long enough description of what happened here."},
        {"category": "SECURITY"},
        {"category": "SECURITY", "description": "too short"},
        {"category": "SECURITY", "description": "                              "},
        {"category": "SECURITY", "description": "x" * 5001},
        {"category": "SECURITY", "description": None},
    ],
    ids=[
        "missing category",
        "unknown category",
        "empty category",
        "missing description",
        "short description",
        "whitespace description",
        "description too long",
        "null description",
    ],
)
def test_bad_report_bodies_get_422(client, body):
    assert client.post("/api/reports", json=body).status_code == 422


def test_description_is_trimmed_before_length_check(submit):
    # 19 real characters padded with spaces must still fail
    assert submit(description="   " + "a" * 19 + "   ").status_code == 422
    assert submit(description="   " + "a" * 20 + "   ").status_code == 201


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/file.pdf",
        "data:text/html,hello",
        "https://",
        "example.com/no-scheme",
        "http://exa mple.com",
        "https://example.com/" + "a" * 2048,
        "http://[::1",
    ],
)
def test_bad_evidence_urls_are_rejected(submit, url):
    assert submit(evidence_url=url).status_code == 422


@pytest.mark.parametrize("url", ["https://drive.google.com/file/d/abc", "http://example.com"])
def test_good_evidence_urls_are_accepted(submit, url):
    assert submit(evidence_url=url).status_code == 201


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_evidence_url_is_stored_as_null(submit, engine, blank):
    assert submit(evidence_url=blank).status_code == 201
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT evidence_url FROM reports").scalar_one() is None


@pytest.mark.parametrize("extra", [{"email": "me@srmist.edu.in"}, {"name": "A"}, {"status": "RESOLVED"}])
def test_extra_fields_are_rejected(submit, extra):
    assert submit(**extra).status_code == 422
