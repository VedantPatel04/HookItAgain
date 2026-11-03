import hashlib
import hmac
import json
import os
import uuid
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from hookitagain.crypto import encrypt_secret
from hookitagain.db.models import (
    Delivery,
    Destination,
    Event,
    OutboxJob,
    Profile,
    Project,
    Source,
    SourceSecret,
    UsageMonthly,
)
from hookitagain.db.session import get_engine
from hookitagain.ingest import handler as ingest_handler
from hookitagain.ingest.handler import ingest
from hookitagain.settings import LambdaSettings, get_settings


def _database_host() -> str | None:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            raw = str(get_settings().database_url)
        except Exception:
            return None
    return urlparse(raw).hostname


@pytest.fixture(scope="session")
def migrated_local_db() -> None:
    host = _database_host()
    if host not in {"localhost", "127.0.0.1"}:
        pytest.skip("ingest tests use local or CI postgres")
    command.upgrade(Config("alembic.ini"), "head")


def _lambda_settings() -> LambdaSettings:
    return LambdaSettings(
        database_url=os.environ.get("DATABASE_URL") or str(get_settings().database_url),
        secret_encryption_key=(
            os.environ.get("SECRET_ENCRYPTION_KEY")
            or get_settings().secret_encryption_key.get_secret_value()
        ),
        delivery_queue_url="https://sqs.us-east-1.amazonaws.com/123/hookitagain-delivery",
    )


def _sign(secret: str, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _seed_source(secret: str) -> Source:
    now = datetime.now(timezone.utc)
    user_id = uuid.uuid4()
    with Session(get_engine()) as session:
        session.add(Profile(user_id=user_id, created_at=now))
        project = Project(user_id=user_id, name="lab", created_at=now)
        session.add(project)
        session.flush()
        destination = Destination(
            project_id=project.id,
            name="staging",
            type="https",
            url="https://example.com/hooks",
            signing_secret_ciphertext=encrypt_secret("destination-secret"),
            created_at=now,
        )
        session.add(destination)
        session.flush()
        source = Source(
            project_id=project.id,
            name="billing-webhooks",
            provider="generic_hmac",
            destination_id=destination.id,
            created_at=now,
        )
        session.add(source)
        session.flush()
        session.add(
            SourceSecret(
                source_id=source.id,
                project_id=project.id,
                ciphertext=encrypt_secret(secret),
                created_at=now,
            )
        )
        session.commit()
        session.refresh(source)
        session.expunge(source)
        return source


def test_unknown_source_is_400(migrated_local_db: None) -> None:
    raw_body = b'{"ok":true}'
    source_id = uuid.uuid4()
    with Session(get_engine()) as session:
        response = ingest(
            source_id,
            raw_body,
            "deliv-1",
            _sign("unused", raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "source_not_found"
    with Session(get_engine()) as session:
        assert session.scalars(select(Event).where(Event.source_id == source_id)).all() == []


def test_bad_hmac_is_401(migrated_local_db: None) -> None:
    secret = "source-secret"
    source = _seed_source(secret)
    raw_body = b'{"ok":true}'
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-bad",
            _sign("wrong-secret", raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"]["code"] == "signature_invalid"
    with Session(get_engine()) as session:
        assert session.scalars(select(Event).where(Event.source_id == source.id)).all() == []


def test_disabled_source_is_400(migrated_local_db: None) -> None:
    secret = "source-secret"
    source = _seed_source(secret)
    with Session(get_engine()) as session:
        row = session.get(Source, source.id)
        assert row is not None
        row.disabled_at = datetime.now(timezone.utc)
        session.commit()
    raw_body = b'{"ok":true}'
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-disabled",
            _sign(secret, raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "source_not_found"
    with Session(get_engine()) as session:
        assert session.scalars(select(Event).where(Event.source_id == source.id)).all() == []


def test_signature_without_sha256_prefix_is_401(migrated_local_db: None) -> None:
    secret = "source-secret"
    source = _seed_source(secret)
    raw_body = b'{"ok":true}'
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-prefix",
            digest,
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"]["code"] == "signature_invalid"


def test_accept_then_duplicate_returns_same_202(migrated_local_db: None) -> None:
    secret = "source-secret"
    source = _seed_source(secret)
    raw_body = b'{"ok":true}'
    signature = _sign(secret, raw_body)
    settings = _lambda_settings()

    with Session(get_engine()) as session:
        first = ingest(
            source.id,
            raw_body,
            "deliv-1",
            signature,
            "push",
            "application/json",
            session,
            settings,
        )
    assert first["statusCode"] == 202
    first_body = json.loads(first["body"])
    assert first_body["event_id"]
    assert first_body["correlation_id"]

    with Session(get_engine()) as session:
        event = session.get(Event, uuid.UUID(first_body["event_id"]))
        assert event is not None
        assert event.status == "accepted"
        assert event.body == raw_body
        assert event.event_type == "push"
        deliveries = session.scalars(
            select(Delivery).where(Delivery.event_id == event.id)
        ).all()
        assert len(deliveries) == 1
        assert deliveries[0].destination_id == source.destination_id
        assert deliveries[0].status == "queued"
        jobs = session.scalars(
            select(OutboxJob).where(OutboxJob.delivery_id == deliveries[0].id)
        ).all()
        assert len(jobs) == 1
        assert jobs[0].status in {"pending", "enqueued"}

    with Session(get_engine()) as session:
        second = ingest(
            source.id,
            raw_body,
            "deliv-1",
            signature,
            "push",
            "application/json",
            session,
            settings,
        )
    assert second["statusCode"] == 202
    assert json.loads(second["body"]) == first_body
    with Session(get_engine()) as session:
        deliveries = session.scalars(
            select(Delivery).where(
                Delivery.event_id == uuid.UUID(first_body["event_id"])
            )
        ).all()
        assert len(deliveries) == 1


def test_user_quota_is_429(migrated_local_db: None) -> None:
    secret = "source-secret"
    source = _seed_source(secret)
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as session:
        row = session.get(Source, source.id)
        assert row is not None
        project = session.get(Project, row.project_id)
        assert project is not None
        session.add(
            UsageMonthly(
                user_id=project.user_id,
                year_month=date(now.year, now.month, 1),
                accepted_count=500,
                updated_at=now,
            )
        )
        session.commit()
    raw_body = b'{"ok":true}'
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-quota",
            _sign(secret, raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 429
    assert json.loads(response["body"])["error"]["code"] == "quota_exceeded"
    with Session(get_engine()) as session:
        assert session.scalars(select(Event).where(Event.source_id == source.id)).all() == []


def test_storage_admission_is_429(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest_handler, "RETAINED_PAYLOAD_BUDGET_BYTES", 0)
    secret = "source-secret"
    source = _seed_source(secret)
    raw_body = b'{"ok":true}'
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-storage",
            _sign(secret, raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 429
    assert json.loads(response["body"])["error"]["code"] == "storage_admission_denied"
    with Session(get_engine()) as session:
        assert session.scalars(select(Event).where(Event.source_id == source.id)).all() == []


def test_sqs_send_ok_marks_outbox_enqueued(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[uuid.UUID] = []
    monkeypatch.setattr(
        ingest_handler,
        "_send_outbox_message",
        lambda queue_url, outbox_job_id: sent.append(outbox_job_id),
    )
    secret = "source-secret"
    source = _seed_source(secret)
    raw_body = b'{"ok":true}'
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-sqs-ok",
            _sign(secret, raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 202
    with Session(get_engine()) as session:
        event = session.get(Event, uuid.UUID(json.loads(response["body"])["event_id"]))
        assert event is not None
        job = session.scalars(
            select(OutboxJob)
            .join(Delivery, OutboxJob.delivery_id == Delivery.id)
            .where(Delivery.event_id == event.id)
        ).one()
        assert job.status == "enqueued"
        assert sent == [job.id]


def test_sqs_send_fail_still_202_and_pending(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(queue_url: str, outbox_job_id: uuid.UUID) -> None:
        raise RuntimeError("sqs down")

    monkeypatch.setattr(ingest_handler, "_send_outbox_message", boom)
    secret = "source-secret"
    source = _seed_source(secret)
    raw_body = b'{"ok":true}'
    with Session(get_engine()) as session:
        response = ingest(
            source.id,
            raw_body,
            "deliv-sqs-fail",
            _sign(secret, raw_body),
            None,
            "application/json",
            session,
            _lambda_settings(),
        )
    assert response["statusCode"] == 202
    with Session(get_engine()) as session:
        event = session.get(Event, uuid.UUID(json.loads(response["body"])["event_id"]))
        assert event is not None
        job = session.scalars(
            select(OutboxJob)
            .join(Delivery, OutboxJob.delivery_id == Delivery.id)
            .where(Delivery.event_id == event.id)
        ).one()
        assert job.status == "pending"
        assert job.last_error is not None
