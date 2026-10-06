from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import case_codes
from app.db import get_db
from app.errors import ApiError
from app.models import Category, Report, utcnow
from app.schemas import CategoryOut, ReportIn, ReporterUpdateOut, ReportStatusOut, SubmitOut

router = APIRouter(prefix="/api", tags=["Reporters"])

SAVE_CODE_NOTE = "Save this code now. It's the only way to check on your report and it can't be recovered."
PRIVACY_TIP = (
    "We don't know who you are, but details in your description that only you would know "
    "can still point back to you."
)


def find_report_by_case_code(
    x_case_code: Annotated[
        str | None,
        Header(
            alias="X-Case-Code",
            description="The case code you got when you submitted, like WD-7K3M-Q9XA-2HFD-R8TN.",
        ),
    ] = None,
    db: Session = Depends(get_db),
) -> Report:
    # The code travels in a header, not the URL, because URLs end up in
    # server logs, proxy logs and browser history.
    if not x_case_code:
        raise ApiError(400, "MISSING_CASE_CODE", "Send your case code in the X-Case-Code header.")
    code = case_codes.normalise(x_case_code)
    if code is None:
        raise ApiError(
            400,
            "INVALID_CASE_CODE",
            "That doesn't look like a case code. It should look like WD-XXXX-XXXX-XXXX-XXXX.",
        )
    report = db.scalar(select(Report).where(Report.case_code_hash == case_codes.hash_code(code)))
    if report is None:
        raise ApiError(404, "CASE_NOT_FOUND", "No report matches that case code.")
    return report


@router.get("/categories", response_model=list[CategoryOut])
def list_categories():
    """The categories a report can be filed under."""
    return [CategoryOut(value=category, label=category.label) for category in Category]


@router.post("/reports", status_code=201, response_model=SubmitOut)
def submit_report(body: ReportIn, db: Session = Depends(get_db)):
    """Submit a report. No account and no name needed. The response holds your case code, shown only once."""
    for _ in range(3):
        code = case_codes.generate()
        report = Report(
            case_code_hash=case_codes.hash_code(code),
            category=body.category,
            description=body.description,
            evidence_url=body.evidence_url,
            submitted_on=utcnow().date(),
        )
        db.add(report)
        try:
            db.commit()
        except IntegrityError:
            # Two codes colliding is a 1 in 2^80 event, but a retry costs nothing.
            db.rollback()
            continue
        return SubmitOut(
            case_code=case_codes.display(code),
            status=report.status,
            submitted_on=report.submitted_on,
            note=SAVE_CODE_NOTE,
            privacy_tip=PRIVACY_TIP,
        )
    raise RuntimeError("Couldn't generate an unused case code after 3 tries")


@router.get("/reports/status", response_model=ReportStatusOut)
def check_status(report: Report = Depends(find_report_by_case_code)):
    """Check on your report with your case code. Shows the status and the updates moderators chose to share."""
    return ReportStatusOut(
        category=report.category,
        status=report.status,
        submitted_on=report.submitted_on,
        closed=report.closed_at is not None,
        updates=[
            ReporterUpdateOut(status=update.to_status, message=update.message, date=update.created_at)
            for update in report.updates
            if update.visible_to_reporter
        ],
    )
