import itertools
import uuid

import pytest
from sqlalchemy import select, update

from app import db
from app.errors import ApiError
from app.models import ALLOWED_MOVES, Report, Status, StatusUpdate, utcnow
from app.routers.moderator import move_report, update_if_unchanged

ALLOWED_PAIRS = [(old, new) for old, moves in ALLOWED_MOVES.items() for new in moves]
REFUSED_PAIRS = [pair for pair in itertools.product(Status, Status) if pair not in ALLOWED_PAIRS]


def patch(client, auth, report_id, **body):
    return client.patch(f"/api/moderator/reports/{report_id}", headers=auth, json=body)


def add_note(client, auth, report_id, **body):
    return client.post(f"/api/moderator/reports/{report_id}/updates", headers=auth, json=body)


def close(client, auth, report_id):
    return client.post(f"/api/moderator/reports/{report_id}/close", headers=auth)


def reporter_view(client, code):
    response = client.get("/api/reports/status", headers={"X-Case-Code": code})
    assert response.status_code == 200
    return response.json()


def test_allowed_moves_are_exactly_the_documented_workflow():
    assert set(ALLOWED_PAIRS) == {
        (Status.SUBMITTED, Status.UNDER_REVIEW),
        (Status.UNDER_REVIEW, Status.RESOLVED),
        (Status.UNDER_REVIEW, Status.DISMISSED),
    }


@pytest.mark.parametrize("old, new", ALLOWED_PAIRS)
def test_each_allowed_move_works(client, auth, make_report, session, old, new):
    report = make_report(status=old)
    response = patch(client, auth, report.id, status=new)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == new
    assert body["updated_at"] is not None
    assert body["updates"][-1]["from_status"] == old
    assert body["updates"][-1]["to_status"] == new
    assert body["updates"][-1]["message"] == f"Status changed to {new.label}."


@pytest.mark.parametrize("old, new", REFUSED_PAIRS)
def test_every_other_move_is_409(client, auth, make_report, old, new):
    report = make_report(status=old)
    response = patch(client, auth, report.id, status=new)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert client.get(f"/api/moderator/reports/{report.id}", headers=auth).json()["status"] == old


def test_refused_move_writes_no_update(client, auth, make_report, session):
    report = make_report()
    patch(client, auth, report.id, status="RESOLVED")
    assert session.scalar(select(StatusUpdate)) is None


def test_skip_review_message_is_helpful(client, auth, make_report):
    report = make_report()
    message = patch(client, auth, report.id, status="RESOLVED").json()["error"]["message"]
    assert message == "Can't move a report from SUBMITTED to RESOLVED. It has to be UNDER_REVIEW first."


def test_status_in_patch_is_case_insensitive(client, auth, make_report):
    report = make_report()
    assert patch(client, auth, report.id, status="under_review").status_code == 200


def test_status_change_with_custom_internal_note(client, auth, make_report):
    report = make_report()
    body = patch(client, auth, report.id, status="UNDER_REVIEW", note="  Asked IT for logs.  ", visible_to_reporter=False).json()
    update = body["updates"][-1]
    assert update["message"] == "Asked IT for logs."
    assert update["visible_to_reporter"] is False
    assert update["moderator"] == "mod"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "CLOSED"},
        {"status": "UNDER_REVIEW", "note": ""},
        {"status": "UNDER_REVIEW", "note": "   "},
        {"status": "UNDER_REVIEW", "note": "x" * 501},
        {"status": "UNDER_REVIEW", "category": "OTHER"},
    ],
)
def test_bad_patch_bodies_get_422(client, auth, make_report, body):
    report = make_report()
    assert patch(client, auth, report.id, **body).status_code == 422


def test_patch_unknown_report_is_404(client, auth):
    assert patch(client, auth, uuid.uuid4(), status="UNDER_REVIEW").status_code == 404


def test_add_visible_and_internal_notes(client, auth, make_report):
    report = make_report()
    visible = add_note(client, auth, report.id, message="We've started looking into this.")
    internal = add_note(client, auth, report.id, message="Probably the night guard.", visible_to_reporter=False)
    assert visible.status_code == internal.status_code == 201
    assert visible.json()["visible_to_reporter"] is True
    assert internal.json()["visible_to_reporter"] is False
    assert visible.json()["from_status"] is None and visible.json()["to_status"] is None

    detail = client.get(f"/api/moderator/reports/{report.id}", headers=auth).json()
    assert [u["message"] for u in detail["updates"]] == ["We've started looking into this.", "Probably the night guard."]
    assert detail["status"] == "SUBMITTED"


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": "  "}, {"message": "x" * 501}, {"message": "ok", "extra": 1}])
def test_bad_note_bodies_get_422(client, auth, make_report, body):
    report = make_report()
    assert add_note(client, auth, report.id, **body).status_code == 422


def test_note_on_unknown_report_is_404(client, auth):
    assert add_note(client, auth, uuid.uuid4(), message="hello").status_code == 404


def test_reporter_sees_visible_updates_but_not_internal_ones_or_moderator(client, make_moderator, submit, session):
    make_moderator(username="inspector_zed")
    token = client.post("/api/auth/login", json={"username": "inspector_zed", "password": "correct-horse-battery"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    code = submit().json()["case_code"]
    report_id = session.scalar(select(Report.id))

    patch(client, auth, report_id, status="UNDER_REVIEW", note="We've started looking into this.")
    add_note(client, auth, report_id, message="Internal: check CCTV for floor 3.", visible_to_reporter=False)
    add_note(client, auth, report_id, message="We've spoken to the lab in charge.")

    view = reporter_view(client, code)
    assert view["status"] == "UNDER_REVIEW"
    assert [u["message"] for u in view["updates"]] == ["We've started looking into this.", "We've spoken to the lab in charge."]
    assert view["updates"][0]["status"] == "UNDER_REVIEW"
    assert view["updates"][1]["status"] is None
    assert set(view["updates"][0]) == {"status", "message", "date"}
    assert "CCTV" not in str(view)
    assert "inspector_zed" not in str(view)
    assert "moderator" not in str(view)


def test_internal_status_change_hides_note_from_reporter(client, auth, submit, session):
    code = submit().json()["case_code"]
    report_id = session.scalar(select(Report.id))
    patch(client, auth, report_id, status="UNDER_REVIEW", note="Looks like spam.", visible_to_reporter=False)
    view = reporter_view(client, code)
    assert view["status"] == "UNDER_REVIEW"
    assert view["updates"] == []


@pytest.mark.parametrize("final", ["RESOLVED", "DISMISSED"])
def test_close_after_final_status(client, auth, make_report, final):
    report = make_report(status=Status(final))
    response = close(client, auth, report.id)
    assert response.status_code == 200
    assert response.json()["closed"] is True
    assert response.json()["closed_at"] is not None
    assert response.json()["status"] == final


@pytest.mark.parametrize("status", [Status.SUBMITTED, Status.UNDER_REVIEW])
def test_close_before_final_status_is_409(client, auth, make_report, status):
    report = make_report(status=status)
    response = close(client, auth, report.id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOT_READY_TO_CLOSE"


def test_closed_case_cannot_change(client, auth, make_report):
    report = make_report(status=Status.RESOLVED)
    assert close(client, auth, report.id).status_code == 200

    for response in [
        patch(client, auth, report.id, status="UNDER_REVIEW"),
        patch(client, auth, report.id, status="DISMISSED"),
        patch(client, auth, report.id, status="RESOLVED"),
        add_note(client, auth, report.id, message="One more thing."),
        add_note(client, auth, report.id, message="Internal one more thing.", visible_to_reporter=False),
        close(client, auth, report.id),
    ]:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CASE_CLOSED"


def test_close_unknown_report_is_404(client, auth):
    assert close(client, auth, uuid.uuid4()).status_code == 404


def test_reporter_sees_closed_case(client, auth, submit, session):
    code = submit().json()["case_code"]
    report_id = session.scalar(select(Report.id))
    patch(client, auth, report_id, status="UNDER_REVIEW")
    patch(client, auth, report_id, status="RESOLVED", note="The door lock has been fixed.")
    close(client, auth, report_id)

    view = reporter_view(client, code)
    assert view["status"] == "RESOLVED"
    assert view["closed"] is True
    assert view["updates"][-1]["message"] == "This case is now closed."


def test_full_path_through_the_workflow(client, auth, make_report):
    report = make_report()
    for status in ["UNDER_REVIEW", "DISMISSED"]:
        assert patch(client, auth, report.id, status=status).status_code == 200
    assert close(client, auth, report.id).status_code == 200
    detail = client.get(f"/api/moderator/reports/{report.id}", headers=auth).json()
    assert [(u["from_status"], u["to_status"]) for u in detail["updates"]] == [
        ("SUBMITTED", "UNDER_REVIEW"),
        ("UNDER_REVIEW", "DISMISSED"),
        (None, None),
    ]


def test_two_moderators_cannot_overwrite_each_other(make_report, moderator, session):
    report = make_report(status=Status.UNDER_REVIEW)

    # Another moderator resolves it after we loaded it but before we save.
    with db.SessionLocal() as other:
        other.execute(update(Report).where(Report.id == report.id).values(status=Status.RESOLVED))
        other.commit()

    # Our copy still says UNDER_REVIEW, so the transition check passes,
    # but the conditional UPDATE has to notice the row changed.
    assert report.status == Status.UNDER_REVIEW
    with pytest.raises(ApiError) as caught:
        move_report(session, report, Status.DISMISSED, moderator, note=None, visible_to_reporter=True)
    assert caught.value.status_code == 409
    assert caught.value.code == "CHANGED_BY_SOMEONE_ELSE"

    with db.SessionLocal() as fresh:
        assert fresh.get(Report, report.id).status == Status.RESOLVED
        assert fresh.scalar(select(StatusUpdate)) is None


def test_note_cannot_sneak_onto_a_case_closed_meanwhile(make_report, session):
    report = make_report(status=Status.RESOLVED)

    # Another moderator closes the case after we loaded it.
    with db.SessionLocal() as other:
        other.execute(update(Report).where(Report.id == report.id).values(closed_at=utcnow()))
        other.commit()

    assert report.closed_at is None
    with pytest.raises(ApiError) as caught:
        update_if_unchanged(session, report, updated_at=utcnow())
    assert caught.value.status_code == 409
