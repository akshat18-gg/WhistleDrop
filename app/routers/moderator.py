import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError, documented
from app.models import ALLOWED_MOVES, FINAL_STATUSES, Moderator, Report, Status, StatusUpdate, utcnow
from app.schemas import (
    ModeratorUpdateOut,
    NoteIn,
    ReportDetail,
    ReportFilters,
    ReportListItem,
    ReportPage,
    StatusChangeIn,
)
from app.security import current_moderator

router = APIRouter(
    prefix="/api/moderator",
    tags=["Moderators"],
    dependencies=[Depends(current_moderator)],
    responses=documented({401: "No token, or the token is invalid or expired"}),
)

NOT_FOUND = {404: "No report has that id"}
BAD_ID = {422: "The id isn't a UUID, or a field failed validation"}

PREVIEW_LENGTH = 200


def preview(text: str) -> str:
    if len(text) <= PREVIEW_LENGTH:
        return text
    return text[: PREVIEW_LENGTH - 1].rstrip() + "…"


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_report_or_404(db: Session, report_id: uuid.UUID) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise ApiError(404, "REPORT_NOT_FOUND", "There's no report with that id.")
    return report


def ensure_not_closed(report: Report) -> None:
    if report.closed_at is not None:
        raise ApiError(409, "CASE_CLOSED", "This case is closed. Nothing on it can be changed.")


def transition_problem(current: Status, new: Status) -> str | None:
    if new in ALLOWED_MOVES[current]:
        return None
    if new == current:
        return f"This report is already {current}."
    if current in FINAL_STATUSES:
        return f"Can't move a report from {current} to {new}. {current} is a final status."
    if current == Status.SUBMITTED and new in ALLOWED_MOVES[Status.UNDER_REVIEW]:
        return f"Can't move a report from {current} to {new}. It has to be UNDER_REVIEW first."
    return f"Can't move a report from {current} back to {new}."


def update_if_unchanged(db: Session, report: Report, **values) -> None:
    # Only touch the row if it still has the status we just checked and is still
    # open. If another moderator got there first, no row matches and we return a
    # conflict instead of quietly overwriting their change.
    result = db.execute(
        update(Report)
        .where(Report.id == report.id, Report.status == report.status, Report.closed_at.is_(None))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ApiError(
            409,
            "CHANGED_BY_SOMEONE_ELSE",
            "Someone else changed this report while you were working on it. Load it again and retry.",
        )


def move_report(
    db: Session,
    report: Report,
    new_status: Status,
    moderator: Moderator,
    note: str | None,
    visible_to_reporter: bool,
) -> None:
    ensure_not_closed(report)
    problem = transition_problem(report.status, new_status)
    if problem:
        raise ApiError(409, "INVALID_STATUS_TRANSITION", problem)

    old_status = report.status
    now = utcnow()
    update_if_unchanged(db, report, status=new_status, updated_at=now)
    db.add(
        StatusUpdate(
            report_id=report.id,
            message=note or f"Status changed to {new_status.label}.",
            from_status=old_status,
            to_status=new_status,
            visible_to_reporter=visible_to_reporter,
            moderator_id=moderator.id,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(report)


def update_out(update: StatusUpdate) -> ModeratorUpdateOut:
    return ModeratorUpdateOut(
        message=update.message,
        from_status=update.from_status,
        to_status=update.to_status,
        visible_to_reporter=update.visible_to_reporter,
        moderator=update.moderator.username,
        created_at=update.created_at,
    )


def report_detail(report: Report) -> ReportDetail:
    return ReportDetail(
        id=report.id,
        category=report.category,
        description=report.description,
        evidence_url=report.evidence_url,
        status=report.status,
        submitted_on=report.submitted_on,
        closed=report.closed_at is not None,
        closed_at=report.closed_at,
        updated_at=report.updated_at,
        updates=[update_out(update) for update in report.updates],
    )


@router.get(
    "/reports",
    response_model=ReportPage,
    summary="List and filter reports",
    responses=documented({422: "A filter has a bad value, or an unknown filter was sent"}),
)
def list_reports(filters: Annotated[ReportFilters, Query()], db: Session = Depends(get_db)):
    """List reports, newest first by default. All filters are optional and can be combined."""
    query = select(Report)
    if filters.status:
        query = query.where(Report.status == filters.status)
    if filters.category:
        query = query.where(Report.category == filters.category)
    if filters.q:
        query = query.where(Report.description.ilike(f"%{escape_like(filters.q)}%", escape="\\"))
    if filters.from_:
        query = query.where(Report.submitted_on >= filters.from_)
    if filters.to:
        query = query.where(Report.submitted_on <= filters.to)

    total = db.scalar(select(func.count()).select_from(query.subquery()))

    by_date = Report.submitted_on.desc() if filters.sort == "newest" else Report.submitted_on.asc()
    # Within one day, order by the random id, so the order says nothing about who submitted first.
    reports = db.scalars(
        query.order_by(by_date, Report.id)
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    ).all()

    return ReportPage(
        items=[
            ReportListItem(
                id=report.id,
                category=report.category,
                status=report.status,
                submitted_on=report.submitted_on,
                closed=report.closed_at is not None,
                description_preview=preview(report.description),
                has_evidence_url=report.evidence_url is not None,
            )
            for report in reports
        ],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
    )


@router.get(
    "/reports/{report_id}",
    response_model=ReportDetail,
    summary="Get one report",
    responses=documented(NOT_FOUND | BAD_ID),
)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    """One report in full, with every update, including internal notes."""
    return report_detail(get_report_or_404(db, report_id))


@router.patch(
    "/reports/{report_id}",
    response_model=ReportDetail,
    summary="Change a report's status",
    responses=documented(
        NOT_FOUND
        | BAD_ID
        | {409: "The move isn't allowed, the case is closed, or someone else changed it first"}
    ),
)
def change_status(
    report_id: uuid.UUID,
    body: StatusChangeIn,
    moderator: Moderator = Depends(current_moderator),
    db: Session = Depends(get_db),
):
    """Move a report to its next status: SUBMITTED to UNDER_REVIEW, then UNDER_REVIEW to RESOLVED or DISMISSED."""
    report = get_report_or_404(db, report_id)
    move_report(db, report, body.status, moderator, body.note, body.visible_to_reporter)
    return report_detail(report)


@router.post(
    "/reports/{report_id}/updates",
    status_code=201,
    response_model=ModeratorUpdateOut,
    summary="Add a note",
    responses=documented(NOT_FOUND | BAD_ID | {409: "The case is closed"}),
)
def add_update(
    report_id: uuid.UUID,
    body: NoteIn,
    moderator: Moderator = Depends(current_moderator),
    db: Session = Depends(get_db),
):
    """Add a note without changing the status. Set visible_to_reporter to false for an internal note."""
    report = get_report_or_404(db, report_id)
    ensure_not_closed(report)
    now = utcnow()
    update_if_unchanged(db, report, updated_at=now)
    note = StatusUpdate(
        report_id=report.id,
        message=body.message,
        visible_to_reporter=body.visible_to_reporter,
        moderator_id=moderator.id,
        created_at=now,
    )
    db.add(note)
    db.commit()
    return update_out(note)


@router.post(
    "/reports/{report_id}/close",
    response_model=ReportDetail,
    summary="Close a case for good",
    responses=documented(
        NOT_FOUND | BAD_ID | {409: "The report isn't RESOLVED or DISMISSED yet, or is already closed"}
    ),
)
def close_report(
    report_id: uuid.UUID,
    moderator: Moderator = Depends(current_moderator),
    db: Session = Depends(get_db),
):
    """Permanently close a RESOLVED or DISMISSED report. After this, nothing on it can change."""
    report = get_report_or_404(db, report_id)
    ensure_not_closed(report)
    if report.status not in FINAL_STATUSES:
        raise ApiError(
            409,
            "NOT_READY_TO_CLOSE",
            f"Only RESOLVED or DISMISSED reports can be closed. This one is {report.status}.",
        )
    now = utcnow()
    update_if_unchanged(db, report, closed_at=now, updated_at=now)
    db.add(
        StatusUpdate(
            report_id=report.id,
            message="This case is now closed.",
            visible_to_reporter=True,
            moderator_id=moderator.id,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(report)
    return report_detail(report)
