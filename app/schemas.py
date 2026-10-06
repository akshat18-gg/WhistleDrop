import uuid
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.models import Category, Status


def _uppercase(value):
    return value.upper() if isinstance(value, str) else value


CategoryIn = Annotated[Category, BeforeValidator(_uppercase)]
StatusIn = Annotated[Status, BeforeValidator(_uppercase)]


class StrictModel(BaseModel):
    # Unknown fields are rejected, so nobody can slip an email or name in alongside a report.
    model_config = ConfigDict(extra="forbid")


class ReportIn(StrictModel):
    category: CategoryIn
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=5000)]
    evidence_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)] | None = None

    @field_validator("evidence_url")
    @classmethod
    def check_evidence_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        problem = "Must be an http:// or https:// link, like https://drive.google.com/..."
        try:
            parts = urlsplit(value)
        except ValueError:
            raise ValueError(problem) from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(problem)
        if any(char.isspace() for char in value):
            raise ValueError("Links can't contain spaces.")
        return value


class CategoryOut(BaseModel):
    value: Category
    label: str


class SubmitOut(BaseModel):
    case_code: str
    status: Status
    submitted_on: date
    note: str
    privacy_tip: str


class ReporterUpdateOut(BaseModel):
    status: Status | None
    message: str
    date: datetime


class ReportStatusOut(BaseModel):
    category: Category
    status: Status
    submitted_on: date
    closed: bool
    updates: list[ReporterUpdateOut]


class LoginIn(StrictModel):
    username: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ReportFilters(StrictModel):
    status: StatusIn | None = None
    category: CategoryIn | None = None
    q: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = Field(
        None, description="Case-insensitive text search in the description."
    )
    from_: date | None = Field(None, alias="from", description="Submitted on or after this date (YYYY-MM-DD).")
    to: date | None = Field(None, description="Submitted on or before this date (YYYY-MM-DD).")
    sort: Literal["newest", "oldest"] = "newest"
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    @field_validator("to")
    @classmethod
    def to_not_before_from(cls, value: date | None, info: ValidationInfo) -> date | None:
        start = info.data.get("from_")
        if value and start and start > value:
            raise ValueError("'to' can't be earlier than 'from'.")
        return value


class ReportListItem(BaseModel):
    id: uuid.UUID
    category: Category
    status: Status
    submitted_on: date
    closed: bool
    description_preview: str
    has_evidence_url: bool


class ReportPage(BaseModel):
    items: list[ReportListItem]
    page: int
    page_size: int
    total: int


class ModeratorUpdateOut(BaseModel):
    message: str
    from_status: Status | None
    to_status: Status | None
    visible_to_reporter: bool
    moderator: str
    created_at: datetime


class ReportDetail(BaseModel):
    id: uuid.UUID
    category: Category
    description: str
    evidence_url: str | None
    status: Status
    submitted_on: date
    closed: bool
    closed_at: datetime | None
    updated_at: datetime | None
    updates: list[ModeratorUpdateOut]
