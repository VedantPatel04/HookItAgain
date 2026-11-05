import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from hookitagain.crypto import encrypt_secret
from hookitagain.db.models import (
    Delivery,
    DeliveryAttempt,
    Destination,
    Event,
    OutboxJob,
    Profile,
    Project,
    Source,
)
from hookitagain.db.session import get_engine
from hookitagain.settings import LambdaSettings, get_settings
from hookitagain.worker import handler as worker_handler
from hookitagain.worker.handler import DeliveryRetry, deliver


def _database_host() -> str | None:  # host of DATABASE_URL
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            raw = str(get_settings().database_url)
        except Exception:
            return None
    return urlparse(raw).hostname


@pytest.fixture(scope="session")
def migrated_local_db() -> None:  # skip unless postgres is localhost, then alembic upgrade
    host = _database_host()
    if host not in {"localhost", "127.0.0.1"}:
        pytest.skip("worker tests use local or CI postgres")
    command.upgrade(Config("alembic.ini"), "head")


def _lambda_settings() -> LambdaSettings:  # LambdaSettings passed into deliver()
    return LambdaSettings(
        database_url=os.environ.get("DATABASE_URL") or str(get_settings().database_url),
        secret_encryption_key=(
            os.environ.get("SECRET_ENCRYPTION_KEY")
            or get_settings().secret_encryption_key.get_secret_value()
        ),
        delivery_queue_url="https://sqs.us-east-1.amazonaws.com/123/hookitagain-delivery",
    )


def _seed_queued_delivery(  # Event, queued Delivery, and OutboxJob for one deliver() call
    *,
    signing_secret: str = "destination-secret",
    raw_body: bytes = b'{"ok":true}',
    url: str = "https://example.com/hooks",
    status: str = "queued",
) -> dict:
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
            url=url,
            signing_secret_ciphertext=encrypt_secret(signing_secret),
            created_at=now,
        )
        session.add(destination)
        session.flush()
        source = Source(
            project_id=project.id,
            name="generic",
            provider="generic_hmac",
            destination_id=destination.id,
            created_at=now,
        )
        session.add(source)
        session.flush()
        event = Event(
            project_id=project.id,
            source_id=source.id,
            provider_delivery_id=str(uuid.uuid4()),
            event_type="ping",
            content_type="application/json",
            body=raw_body,
            byte_size=len(raw_body),
            status="accepted",
            correlation_id=uuid.uuid4(),
            accepted_at=now,
        )
        session.add(event)
        session.flush()
        delivery = Delivery(
            project_id=project.id,
            event_id=event.id,
            destination_id=destination.id,
            status=status,
            release_after=now,
            created_at=now,
            updated_at=now,
        )
        session.add(delivery)
        session.flush()
        outbox = OutboxJob(
            project_id=project.id,
            delivery_id=delivery.id,
            status="pending",
            release_after=now,
            created_at=now,
        )
        session.add(outbox)
        session.commit()
        return {
            "outbox_id": outbox.id,
            "delivery_id": delivery.id,
            "event_id": event.id,
            "destination_id": destination.id,
            "signing_secret": signing_secret,
            "raw_body": raw_body,
            "correlation_id": event.correlation_id,
        }


# 2xx success
# mock POST 200, call deliver
# one DeliveryAttempt, error_category null, Delivery succeeded, do not raise
# POST body is Event.body, HMAC is {timestamp}.{raw_body} with the destination signing secret
def test_2xx_records_one_attempt_and_succeeds(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted = []

    class Response:
        status_code = 200
        content = b"ok"

    def fake_post(
        url,
        raw_body,
        content_type,
        timestamp,
        signature,
        correlation_id,
        event_id,
        delivery_id,
    ):
        posted.append(
            {
                "url": url,
                "raw_body": raw_body,
                "timestamp": timestamp,
                "signature": signature,
            }
        )
        return Response()

    monkeypatch.setattr(worker_handler, "_post_to_destination", fake_post)
    ids = _seed_queued_delivery()
    with Session(get_engine()) as session:
        deliver(ids["outbox_id"], 1, session, _lambda_settings())

    with Session(get_engine()) as session:
        attempts = session.scalars(
            select(DeliveryAttempt).where(
                DeliveryAttempt.delivery_id == ids["delivery_id"]
            )
        ).all()
        delivery = session.get(Delivery, ids["delivery_id"])

    assert delivery is not None
    assert delivery.status == "succeeded"
    assert len(attempts) == 1
    assert attempts[0].error_category is None
    assert attempts[0].http_status == 200
    assert attempts[0].attempt_number == 1
    assert len(posted) == 1
    call = posted[0]
    assert call["raw_body"] == ids["raw_body"]
    assert call["url"] == "https://example.com/hooks"
    material = f"{call['timestamp']}.".encode("utf-8") + ids["raw_body"]
    digest = hmac.new(
        ids["signing_secret"].encode("utf-8"), material, hashlib.sha256
    ).hexdigest()
    assert call["signature"] == f"sha256={digest}"


# already succeeded
# mock POST so the test fails if it is called
# deliver a Delivery that is already succeeded
# no new attempt, status still succeeded, do not raise
def test_already_succeeded_does_not_post(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_post(*args, **kwargs):
        raise AssertionError("POST must not run for a succeeded Delivery")

    monkeypatch.setattr(worker_handler, "_post_to_destination", fake_post)
    ids = _seed_queued_delivery(status="succeeded")
    with Session(get_engine()) as session:
        deliver(ids["outbox_id"], 1, session, _lambda_settings())

    with Session(get_engine()) as session:
        attempts = session.scalars(
            select(DeliveryAttempt).where(
                DeliveryAttempt.delivery_id == ids["delivery_id"]
            )
        ).all()
        delivery = session.get(Delivery, ids["delivery_id"])

    assert delivery is not None
    assert delivery.status == "succeeded"
    assert attempts == []


# non-2xx (500)
# mock POST 500, deliver attempt_number 1
# one attempt with destination_5xx, Delivery queued, raise DeliveryRetry
def test_non_2xx_queues_and_raises(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status_code = 500
        content = b"err"

    def fake_post(*args, **kwargs):
        return Response()

    monkeypatch.setattr(worker_handler, "_post_to_destination", fake_post)
    ids = _seed_queued_delivery()
    with Session(get_engine()) as session:
        with pytest.raises(DeliveryRetry):
            deliver(ids["outbox_id"], 1, session, _lambda_settings())

    with Session(get_engine()) as session:
        attempts = session.scalars(
            select(DeliveryAttempt).where(
                DeliveryAttempt.delivery_id == ids["delivery_id"]
            )
        ).all()
        delivery = session.get(Delivery, ids["delivery_id"])

    assert delivery is not None
    assert delivery.status == "queued"
    assert len(attempts) == 1
    assert attempts[0].error_category == "destination_5xx"
    assert attempts[0].http_status == 500
    assert attempts[0].attempt_number == 1


# fifth failed receive
# mock POST 500, deliver attempt_number 5
# Delivery dead_lettered, attempt_number 5, raise DeliveryRetry
def test_fifth_failed_receive_is_dead_lettered(
    migrated_local_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status_code = 500
        content = b"err"

    def fake_post(*args, **kwargs):
        return Response()

    monkeypatch.setattr(worker_handler, "_post_to_destination", fake_post)
    ids = _seed_queued_delivery()
    with Session(get_engine()) as session:
        with pytest.raises(DeliveryRetry):
            deliver(ids["outbox_id"], 5, session, _lambda_settings())

    with Session(get_engine()) as session:
        attempts = session.scalars(
            select(DeliveryAttempt).where(
                DeliveryAttempt.delivery_id == ids["delivery_id"]
            )
        ).all()
        delivery = session.get(Delivery, ids["delivery_id"])

    assert delivery is not None
    assert delivery.status == "dead_lettered"
    assert len(attempts) == 1
    assert attempts[0].error_category == "destination_5xx"
    assert attempts[0].http_status == 500
    assert attempts[0].attempt_number == 5
