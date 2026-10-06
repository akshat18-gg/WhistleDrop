from datetime import datetime, timezone

from app.models import Status


def test_stats_on_an_empty_database(client, auth):
    response = client.get("/api/moderator/stats", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == body["open"] == body["closed"] == 0
    assert body["by_status"] == {"SUBMITTED": 0, "UNDER_REVIEW": 0, "RESOLVED": 0, "DISMISSED": 0}
    assert set(body["by_category"]) == {"SECURITY", "HARASSMENT", "CORRUPTION", "TECHNICAL", "OTHER"}


def test_stats_count_by_status_and_category(client, auth, make_report):
    closed_at = datetime(2026, 10, 5, tzinfo=timezone.utc)
    make_report("SECURITY")
    make_report("SECURITY", status=Status.UNDER_REVIEW)
    make_report("HARASSMENT", status=Status.UNDER_REVIEW)
    make_report("CORRUPTION", status=Status.RESOLVED, closed_at=closed_at)
    make_report("OTHER", status=Status.DISMISSED)

    body = client.get("/api/moderator/stats", headers=auth).json()
    assert body["total"] == 5
    assert body["open"] == 3
    assert body["closed"] == 1
    assert body["by_status"] == {"SUBMITTED": 1, "UNDER_REVIEW": 2, "RESOLVED": 1, "DISMISSED": 1}
    assert body["by_category"] == {"SECURITY": 2, "HARASSMENT": 1, "CORRUPTION": 1, "TECHNICAL": 0, "OTHER": 1}
