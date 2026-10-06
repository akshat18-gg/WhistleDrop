import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

SECRET_TEXT = "The hostel warden in block D is taking money to change room allotments."
SECRET_URL = "https://drive.google.com/file/d/very-private-evidence/view"


def raw_report_row(engine):
    with engine.connect() as conn:
        return conn.exec_driver_sql("SELECT description, evidence_url FROM reports").one()


def test_description_and_link_are_encrypted_on_disk(submit, engine, tmp_path):
    submit(description=SECRET_TEXT, evidence_url=SECRET_URL)
    description, evidence_url = raw_report_row(engine)
    assert description.startswith("gAAAAA")
    assert evidence_url.startswith("gAAAAA")
    assert "warden" not in description
    assert "very-private" not in evidence_url

    raw_file = (tmp_path / "test.db").read_bytes()
    assert b"warden" not in raw_file
    assert b"very-private" not in raw_file


def test_tokens_do_not_carry_the_submission_time(submit, engine):
    submit(description=SECRET_TEXT, evidence_url=SECRET_URL)
    for token in raw_report_row(engine):
        assert Fernet(get_settings().encryption_key).extract_timestamp(token.encode()) == 0


def test_a_copied_database_is_useless_without_the_key(submit, engine):
    submit(description=SECRET_TEXT)
    description, _ = raw_report_row(engine)
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(description)


def test_moderators_still_read_plain_text(client, auth, submit):
    submit(description=SECRET_TEXT, evidence_url=SECRET_URL)
    listed = client.get("/api/moderator/reports", headers=auth).json()["items"][0]
    assert listed["description_preview"] == SECRET_TEXT
    detail = client.get(f"/api/moderator/reports/{listed['id']}", headers=auth).json()
    assert detail["description"] == SECRET_TEXT
    assert detail["evidence_url"] == SECRET_URL


def test_search_works_on_encrypted_descriptions(client, auth, submit):
    submit(description=SECRET_TEXT)
    submit(description="Someone keeps parking in the fire lane outside the library.")
    body = client.get("/api/moderator/reports", headers=auth, params={"q": "WARDEN"}).json()
    assert body["total"] == 1
    assert "warden" in body["items"][0]["description_preview"]


def test_missing_evidence_link_stays_null(submit, engine):
    submit()
    assert raw_report_row(engine)[1] is None
