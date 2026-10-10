import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ATTEMPT_ERROR_CATEGORIES = (
    "destination_timeout",
    "destination_connect_error",
    "destination_4xx",
    "destination_5xx",
    "redirect_blocked",
    "ssrf_blocked",
    "cli_disconnected",
    "cli_timeout",
    "worker_unhandled",
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)


def uuid_pk() -> Mapped[uuid.UUID]:
    """UUID primary key filled by Postgres gen_random_uuid()."""
    return mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def timestamptz(*, nullable: bool = False) -> Mapped[datetime]:
    """Timezone-aware timestamp column matching TIMESTAMPTZ in the contract."""
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    return mapped_column(DateTime(timezone=True), nullable=False)


class Profile(Base):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    github_login: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamptz()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.user_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = timestamptz()


class Destination(Base):
    __tablename__ = "destinations"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        CheckConstraint("type IN ('https', 'cli')", name="type"),
        CheckConstraint(
            "(type = 'https' AND url IS NOT NULL) OR (type = 'cli' AND url IS NULL)",
            name="type_url",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    signing_secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = timestamptz()


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        CheckConstraint("provider IN ('generic_hmac', 'github')", name="provider"),
        ForeignKeyConstraint(
            ["project_id", "destination_id"],
            ["destinations.project_id", "destinations.id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    disabled_at: Mapped[datetime | None] = timestamptz(nullable=True)
    created_at: Mapped[datetime] = timestamptz()


class SourceSecret(Base):
    __tablename__ = "source_secrets"
    __table_args__ = (
        UniqueConstraint("project_id", "source_id"),
        ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["sources.project_id", "sources.id"],
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = timestamptz()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("source_id", "provider_delivery_id"),
        CheckConstraint("byte_size >= 0 AND byte_size <= 65536", name="byte_size"),
        CheckConstraint("status IN ('accepted', 'payload_purged')", name="status"),
        CheckConstraint(
            "(status = 'accepted' AND body IS NOT NULL) OR "
            "(status = 'payload_purged' AND body IS NULL AND purged_at IS NOT NULL)",
            name="purge",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["sources.project_id", "sources.id"],
        ),
        Index("ix_events_project_accepted_at", "project_id", text("accepted_at DESC")),
        Index(
            "ix_events_project_source_accepted_at",
            "project_id",
            "source_id",
            text("accepted_at DESC"),
        ),
        Index(
            "ix_events_project_event_type_accepted_at",
            "project_id",
            "event_type",
            text("accepted_at DESC"),
        ),
        Index(
            "ix_events_project_status_accepted_at",
            "project_id",
            "status",
            text("accepted_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider_delivery_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    accepted_at: Mapped[datetime] = timestamptz()
    purged_at: Mapped[datetime | None] = timestamptz(nullable=True)


class ScenarioSuite(Base):
    __tablename__ = "scenario_suites"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        CheckConstraint(
            "plan_kind IN ('duplicate', 'delay', 'reorder')", name="plan_kind"
        ),
        ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["sources.project_id", "sources.id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "destination_id"],
            ["destinations.project_id", "destinations.id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    plan_kind: Mapped[str] = mapped_column(Text, nullable=False)
    plan_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = timestamptz()


class SuiteRun(Base):
    __tablename__ = "suite_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("suite_id", "scheduled_for"),
        CheckConstraint(
            "status IN ('scheduled', 'running', 'completed', 'failed')",
            name="status",
        ),
        ForeignKeyConstraint(
            ["project_id", "suite_id"],
            ["scenario_suites.project_id", "scenario_suites.id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    suite_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scheduled_for: Mapped[datetime] = timestamptz()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamptz()
    started_at: Mapped[datetime | None] = timestamptz(nullable=True)
    finished_at: Mapped[datetime | None] = timestamptz(nullable=True)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        CheckConstraint(
            "status IN ('queued', 'in_flight', 'succeeded', 'dead_lettered')",
            name="status",
        ),
        ForeignKeyConstraint(
            ["project_id", "event_id"],
            ["events.project_id", "events.id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "destination_id"],
            ["destinations.project_id", "destinations.id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "replayed_from_delivery_id"],
            ["deliveries.project_id", "deliveries.id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "suite_run_id"],
            ["suite_runs.project_id", "suite_runs.id"],
        ),
        Index("ix_deliveries_event_id", "event_id"),
        Index(
            "ix_deliveries_open_status",
            "status",
            postgresql_where=text("status IN ('queued', 'in_flight')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    replayed_from_delivery_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    suite_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    release_after: Mapped[datetime] = timestamptz()
    created_at: Mapped[datetime] = timestamptz()
    updated_at: Mapped[datetime] = timestamptz()


class OutboxJob(Base):
    __tablename__ = "outbox_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("delivery_id"),
        CheckConstraint("status IN ('pending', 'enqueued')", name="status"),
        ForeignKeyConstraint(
            ["project_id", "delivery_id"],
            ["deliveries.project_id", "deliveries.id"],
        ),
        Index(
            "ix_outbox_jobs_pending",
            "status",
            "release_after",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    delivery_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    release_after: Mapped[datetime] = timestamptz()
    created_at: Mapped[datetime] = timestamptz()
    enqueued_at: Mapped[datetime | None] = timestamptz(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint("project_id", "id"),
        UniqueConstraint("delivery_id", "attempt_number"),
        CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 5", name="attempt_number"
        ),
        CheckConstraint("duration_ms >= 0", name="duration_ms"),
        CheckConstraint(
            "response_excerpt IS NULL OR char_length(response_excerpt) <= 2048",
            name="response_excerpt",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN ("
            + ", ".join(f"'{c}'" for c in ATTEMPT_ERROR_CATEGORIES)
            + ")",
            name="error_category",
        ),
        CheckConstraint(
            "(error_category IS NULL AND http_status BETWEEN 200 AND 299) OR "
            "(error_category IS NOT NULL)",
            name="attempt_outcome",
        ),
        ForeignKeyConstraint(
            ["project_id", "delivery_id"],
            ["deliveries.project_id", "deliveries.id"],
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    delivery_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_category: Mapped[str | None] = mapped_column(Text)
    response_excerpt: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    completed_at: Mapped[datetime] = timestamptz()


class UsageMonthly(Base):
    __tablename__ = "usage_monthly"
    __table_args__ = (
        CheckConstraint("accepted_count >= 0", name="accepted_count"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.user_id"), primary_key=True
    )
    year_month: Mapped[date] = mapped_column(Date, primary_key=True)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = timestamptz()


class UsageGlobalMonthly(Base):
    __tablename__ = "usage_global_monthly"
    __table_args__ = (
        CheckConstraint("accepted_count >= 0", name="accepted_count"),
    )

    year_month: Mapped[date] = mapped_column(Date, primary_key=True)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = timestamptz()
