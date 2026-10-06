import re

from sqlalchemy import inspect


def test_reports_table_has_no_identity_columns(engine):
    columns = {c["name"] for c in inspect(engine).get_columns("reports")}
    assert columns == {
        "id",
        "case_code_hash",
        "category",
        "description",
        "evidence_url",
        "status",
        "submitted_on",
        "closed_at",
        "updated_at",
    }
    for forbidden in ["ip", "ip_address", "user_agent", "email", "name", "phone", "device"]:
        assert forbidden not in columns


def test_submitted_on_is_a_date_without_time(submit, client, engine):
    body = submit().json()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["submitted_on"])

    status = client.get("/api/reports/status", headers={"X-Case-Code": body["case_code"]}).json()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", status["submitted_on"])

    column = next(c for c in inspect(engine).get_columns("reports") if c["name"] == "submitted_on")
    assert str(column["type"]) == "DATE"
    with engine.connect() as conn:
        stored = conn.exec_driver_sql("SELECT submitted_on, updated_at FROM reports").one()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", stored[0])
    assert stored[1] is None


def test_reports_table_has_no_rowid(engine):
    with engine.connect() as conn:
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'reports'").scalar_one()
    assert "WITHOUT ROWID" in sql


def test_status_response_does_not_include_description(submit, client):
    code = submit().json()["case_code"]
    body = client.get("/api/reports/status", headers={"X-Case-Code": code}).json()
    assert "description" not in body
    assert "propped open" not in str(body)
