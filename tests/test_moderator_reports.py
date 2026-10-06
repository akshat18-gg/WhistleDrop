import uuid
from datetime import date

import pytest

from app.models import Status

LONG_TEXT = "Someone has been copying exam papers from the staff room printer. " * 10


def list_reports(client, auth, **params):
    response = client.get("/api/moderator/reports", headers=auth, params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def mixed_reports(make_report):
    """Six reports across categories, statuses and dates."""
    return [
        make_report("SECURITY", "Door to the server room is never locked at night.", submitted_on=date(2026, 9, 1)),
        make_report("SECURITY", "Lab computers still use the default admin password.", status=Status.UNDER_REVIEW, submitted_on=date(2026, 9, 10)),
        make_report("HARASSMENT", "A senior keeps sending threatening messages in the club group.", submitted_on=date(2026, 9, 10)),
        make_report("CORRUPTION", "Fest sponsorship money is not showing up in the accounts.", status=Status.UNDER_REVIEW, submitted_on=date(2026, 9, 20)),
        make_report("TECHNICAL", "The attendance portal shows other students' records.", status=Status.RESOLVED, submitted_on=date(2026, 10, 1)),
        make_report("OTHER", "Hostel mess food has made several students sick.", status=Status.DISMISSED, submitted_on=date(2026, 10, 5)),
    ]


def test_list_returns_everything_with_previews(client, auth, mixed_reports):
    body = list_reports(client, auth)
    assert body["total"] == 6
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 6
    item = body["items"][0]
    assert set(item) == {"id", "category", "status", "submitted_on", "closed", "description_preview", "has_evidence_url"}


def test_filter_by_status(client, auth, mixed_reports):
    body = list_reports(client, auth, status="UNDER_REVIEW")
    assert body["total"] == 2
    assert {item["status"] for item in body["items"]} == {"UNDER_REVIEW"}


def test_filter_by_category(client, auth, mixed_reports):
    body = list_reports(client, auth, category="SECURITY")
    assert body["total"] == 2
    assert {item["category"] for item in body["items"]} == {"SECURITY"}


def test_filter_by_status_and_category_together(client, auth, mixed_reports):
    body = list_reports(client, auth, status="UNDER_REVIEW", category="SECURITY")
    assert body["total"] == 1
    assert body["items"][0]["description_preview"].startswith("Lab computers")


def test_filters_are_case_insensitive(client, auth, mixed_reports):
    assert list_reports(client, auth, status="under_review", category="security")["total"] == 1


def test_text_search_is_case_insensitive(client, auth, mixed_reports):
    body = list_reports(client, auth, q="SERVER ROOM")
    assert body["total"] == 1
    assert "server room" in body["items"][0]["description_preview"]


def test_text_search_treats_wildcards_literally(client, auth, make_report):
    make_report(description="Canteen prices went up by 50% overnight with no notice.")
    make_report(description="Canteen prices went up by 50 rupees overnight with no notice.")
    assert list_reports(client, auth, q="50%")["total"] == 1
    assert list_reports(client, auth, q="%")["total"] == 1
    assert list_reports(client, auth, q="_")["total"] == 0


def test_text_search_combines_with_other_filters(client, auth, mixed_reports):
    assert list_reports(client, auth, q="the", category="TECHNICAL")["total"] == 1


def test_date_range(client, auth, mixed_reports):
    assert list_reports(client, auth, **{"from": "2026-09-10", "to": "2026-09-20"})["total"] == 3
    assert list_reports(client, auth, **{"from": "2026-10-01"})["total"] == 2
    assert list_reports(client, auth, to="2026-09-01")["total"] == 1
    assert list_reports(client, auth, **{"from": "2026-09-10", "to": "2026-09-10"})["total"] == 2


def test_sort_newest_and_oldest(client, auth, mixed_reports):
    newest = [item["submitted_on"] for item in list_reports(client, auth)["items"]]
    oldest = [item["submitted_on"] for item in list_reports(client, auth, sort="oldest")["items"]]
    assert newest == sorted(newest, reverse=True)
    assert oldest == sorted(oldest)


def test_same_day_reports_are_ordered_by_random_id(client, auth, make_report):
    reports = [make_report(submitted_on=date(2026, 9, 1)) for _ in range(8)]
    ids = [item["id"] for item in list_reports(client, auth)["items"]]
    assert ids == sorted(str(r.id) for r in reports)


def test_pagination_totals(client, auth, make_report):
    for _ in range(25):
        make_report()
    pages = [list_reports(client, auth, page=n, page_size=10) for n in (1, 2, 3, 4)]
    assert [p["total"] for p in pages] == [25, 25, 25, 25]
    assert [len(p["items"]) for p in pages] == [10, 10, 5, 0]
    all_ids = [item["id"] for p in pages for item in p["items"]]
    assert len(set(all_ids)) == 25


def test_pagination_total_respects_filters(client, auth, make_report):
    for _ in range(7):
        make_report(category="CORRUPTION")
    for _ in range(4):
        make_report(category="OTHER")
    body = list_reports(client, auth, category="CORRUPTION", page_size=5, page=2)
    assert body["total"] == 7
    assert len(body["items"]) == 2


def test_preview_is_cut_to_200_characters(client, auth, make_report):
    make_report(description=LONG_TEXT)
    preview = list_reports(client, auth)["items"][0]["description_preview"]
    assert len(preview) <= 200
    assert preview.endswith("…")
    assert LONG_TEXT.startswith(preview[:-1])


@pytest.mark.parametrize(
    "params",
    [
        {"status": "OPEN"},
        {"category": "PARKING"},
        {"page": 0},
        {"page": "two"},
        {"page_size": 0},
        {"page_size": 101},
        {"from": "2026-13-01"},
        {"to": "yesterday"},
        {"from": "2026-10-05", "to": "2026-10-01"},
        {"sort": "random"},
        {"q": "x" * 201},
        {"email": "someone@example.com"},
    ],
)
def test_bad_filters_get_422(client, auth, params):
    response = client.get("/api/moderator/reports", headers=auth, params=params)
    assert response.status_code == 422


def test_report_detail_shows_full_report(client, auth, make_report):
    report = make_report(category="TECHNICAL", description=LONG_TEXT)
    response = client.get(f"/api/moderator/reports/{report.id}", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["description"] == LONG_TEXT.strip()
    assert body["category"] == "TECHNICAL"
    assert body["status"] == "SUBMITTED"
    assert body["closed"] is False
    assert body["updates"] == []


def test_report_detail_unknown_id_is_404(client, auth):
    response = client.get(f"/api/moderator/reports/{uuid.uuid4()}", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPORT_NOT_FOUND"


@pytest.mark.parametrize("bad_id", ["123", "not-a-uuid", "1' OR '1'='1"])
def test_report_detail_bad_id_is_422(client, auth, bad_id):
    assert client.get(f"/api/moderator/reports/{bad_id}", headers=auth).status_code == 422
