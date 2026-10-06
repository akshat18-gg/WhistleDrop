from datetime import date, datetime
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints, field_validator

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
