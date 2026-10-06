import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.models import Report
from app.schemas import ModeratorUpdateOut, ReportDetail, ReportFilters, ReportListItem, ReportPage
from app.security import current_moderator

router = APIRouter(
    prefix="/api/moderator",
    tags=["Moderators"],
    dependencies=[Depends(current_moderator)],
)

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
        updates=[
            ModeratorUpdateOut(
                message=update.message,
                from_status=update.from_status,
                to_status=update.to_status,
                visible_to_reporter=update.visible_to_reporter,
                moderator=update.moderator.username,
                created_at=update.created_at,
            )
            for update in report.updates
        ],
    )


@router.get("/reports", response_model=ReportPage)
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


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    """One report in full, with every update, including internal notes."""
    return report_detail(get_report_or_404(db, report_id))
