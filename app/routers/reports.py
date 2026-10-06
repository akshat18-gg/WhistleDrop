import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import case_codes, evidence
from app.db import get_db
from app.errors import ApiError, documented
from app.models import Category, EvidenceFile, Report, utcnow
from app.schemas import (
    CategoryOut,
    EvidenceUploadOut,
    ReportIn,
    ReporterUpdateOut,
    ReportStatusOut,
    SubmitOut,
)
from app.security import limiter

router = APIRouter(prefix="/api", tags=["Reporters"])

SAVE_CODE_NOTE = "Save this code now. It's the only way to check on your report and it can't be recovered."
PRIVACY_TIP = (
    "We don't know who you are, but details in your description that only you would know "
    "can still point back to you."
)
IMAGE_NOTE = "Hidden details like GPS location, camera model and the time the photo was taken were removed before saving."
PDF_NOTE = (
    "Careful: PDFs can hold hidden details like the author's name and the program that made them, "
    "and those were not removed. If that could identify you, upload screenshots instead."
)

# The code travels in a header, not the URL, because URLs end up in
# server logs, proxy logs and browser history.
CaseCodeHeader = Annotated[
    str | None,
    Header(
        alias="X-Case-Code",
        description="Required. The case code you got when you submitted, like WD-7K3M-Q9XA-2HFD-R8TN.",
    ),
]


def find_report_by_case_code(db: Session, raw_code: str | None) -> Report:
    if not raw_code:
        raise ApiError(400, "MISSING_CASE_CODE", "Send your case code in the X-Case-Code header.")
    code = case_codes.normalise(raw_code)
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


@router.get("/categories", response_model=list[CategoryOut], summary="List categories")
def list_categories():
    """The categories a report can be filed under."""
    return [CategoryOut(value=category, label=category.label) for category in Category]


@router.post(
    "/reports",
    status_code=201,
    response_model=SubmitOut,
    summary="Submit a report",
    responses=documented(
        {
            400: "The body isn't valid JSON",
            413: "The body is over 32 KB",
            422: "A field failed validation, or an unknown field was sent",
            429: "More than 10 reports in an hour from the same client",
        }
    ),
)
@limiter.limit("10/hour")
def submit_report(request: Request, body: ReportIn, db: Session = Depends(get_db)):
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


@router.get(
    "/reports/status",
    response_model=ReportStatusOut,
    summary="Check on your report",
    responses=documented(
        {
            400: "The X-Case-Code header is missing or isn't shaped like a case code",
            404: "No report matches that case code",
            429: "More than 30 checks in a minute from the same client",
        }
    ),
)
@limiter.limit("30/minute")
def check_status(
    request: Request,
    x_case_code: CaseCodeHeader = None,
    db: Session = Depends(get_db),
):
    """Check on your report with your case code. Shows the status and the updates moderators chose to share."""
    # Looked up here rather than in a dependency, so the rate limit above
    # also counts lookups that fail with 400 or 404.
    report = find_report_by_case_code(db, x_case_code)
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


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post(
    "/reports/evidence",
    status_code=201,
    response_model=EvidenceUploadOut,
    summary="Attach a file to your report",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    responses=documented(
        {
            400: "No file sent, or the X-Case-Code header is missing or badly formatted",
            404: "No report matches that case code",
            409: "The case is closed, or it already has 5 files",
            413: "The file is over 5 MB",
            415: "The file isn't a JPEG, PNG or PDF",
            422: "The image is damaged or has too many pixels",
            429: "More than 10 uploads in an hour from the same client",
        }
    ),
)
@limiter.limit("10/hour")
def upload_evidence(
    request: Request,
    data: bytes = Depends(raw_body),
    x_case_code: CaseCodeHeader = None,
    db: Session = Depends(get_db),
):
    """Send a JPEG, PNG or PDF (up to 5 MB) as the raw request body. Images have their metadata removed.
    Only moderators can download it."""
    report = find_report_by_case_code(db, x_case_code)
    if report.closed_at is not None:
        raise ApiError(409, "CASE_CLOSED", "This case is closed. Nothing on it can be changed.")
    if len(report.evidence) >= evidence.MAX_FILES_PER_REPORT:
        raise ApiError(
            409, "TOO_MANY_FILES", f"A report can have at most {evidence.MAX_FILES_PER_REPORT} files."
        )

    content_type, cleaned = evidence.clean_upload(data)
    item = EvidenceFile(
        id=uuid.uuid4(),
        content_type=content_type,
        size_bytes=len(cleaned),
        uploaded_on=utcnow().date(),
    )
    evidence.save(item.id, cleaned)
    report.evidence.append(item)
    try:
        db.commit()
    except Exception:
        evidence.delete(item.id)
        raise

    return EvidenceUploadOut(
        content_type=content_type,
        size_bytes=len(cleaned),
        files_attached=len(report.evidence),
        note=PDF_NOTE if content_type == "application/pdf" else IMAGE_NOTE,
    )
