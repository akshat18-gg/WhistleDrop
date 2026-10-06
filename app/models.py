import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, TypeDecorator, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Category(enum.StrEnum):
    SECURITY = "SECURITY"
    HARASSMENT = "HARASSMENT"
    CORRUPTION = "CORRUPTION"
    TECHNICAL = "TECHNICAL"
    OTHER = "OTHER"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class Status(enum.StrEnum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


# Every status a report can move to from each status. Anything not listed here is refused.
ALLOWED_MOVES: dict[Status, set[Status]] = {
    Status.SUBMITTED: {Status.UNDER_REVIEW},
    Status.UNDER_REVIEW: {Status.RESOLVED, Status.DISMISSED},
    Status.RESOLVED: set(),
    Status.DISMISSED: set(),
}
FINAL_STATUSES = {status for status, moves in ALLOWED_MOVES.items() if not moves}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def enum_type(enum_class):
    return Enum(enum_class, native_enum=False, create_constraint=True)


class UTCDateTime(TypeDecorator):
    """SQLite has no timezone support, so store naive UTC and mark it as UTC when reading it back."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Report(Base):
    __tablename__ = "reports"
    # A normal SQLite table has a hidden rowid that counts up with every insert,
    # which would record the order reports came in. Without it, rows are kept
    # in order of the random UUID.
    __table_args__ = {"sqlite_with_rowid": False}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[Category] = mapped_column(enum_type(Category))
    description: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[Status] = mapped_column(enum_type(Status), default=Status.SUBMITTED)
    # Date only. An exact time could be matched to whoever was at their desk at 10:42.
    submitted_on: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Left empty until a moderator changes something, so it never holds the submission time.
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    updates: Mapped[list["StatusUpdate"]] = relationship(
        back_populates="report", order_by="StatusUpdate.id"
    )


class StatusUpdate(Base):
    __tablename__ = "status_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    message: Mapped[str] = mapped_column(String(500))
    from_status: Mapped[Status | None] = mapped_column(enum_type(Status))
    to_status: Mapped[Status | None] = mapped_column(enum_type(Status))
    visible_to_reporter: Mapped[bool] = mapped_column(default=True)
    moderator_id: Mapped[int] = mapped_column(ForeignKey("moderators.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    report: Mapped[Report] = relationship(back_populates="updates")
    moderator: Mapped["Moderator"] = relationship()


class Moderator(Base):
    __tablename__ = "moderators"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
