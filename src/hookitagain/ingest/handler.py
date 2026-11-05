import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import date, datetime, timezone

import boto3

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from hookitagain.crypto import decrypt_secret
from hookitagain.metrics import emit_count, emit_ms
from hookitagain.db.models import (
    Delivery,
    Event,
    OutboxJob,
    Project,
    Source,
    SourceSecret,
    UsageGlobalMonthly,
    UsageMonthly,
)
from hookitagain.db.session import get_engine
from hookitagain.settings import LambdaSettings, get_lambda_settings

USER_MONTHLY_EVENT_CAP = 500
GLOBAL_MONTHLY_EVENT_CAP = 10_000
RETAINED_PAYLOAD_BUDGET_BYTES = 209_715_200


def _lambda_error(status_code: int, code: str, message: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": {"code": code, "message": message}}),
    }


def _accepted_202(event_id: uuid.UUID, correlation_id: uuid.UUID) -> dict:
    return {
        "statusCode": 202,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {"event_id": str(event_id), "correlation_id": str(correlation_id)}
        ),
    }


def _signature_matches(secret: str, raw_body: bytes, signature_header: str) -> bool:
    prefix = "sha256="
    if not signature_header.lower().startswith(prefix):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    got = signature_header[len(prefix) :].lower()
    return hmac.compare_digest(got, expected)


def _send_outbox_message(queue_url: str, outbox_job_id: uuid.UUID) -> None:
    boto3.client("sqs").send_message(
        QueueUrl=queue_url,
        MessageBody=str(outbox_job_id),
    )


def handle(event, context):
    # Read the Lambda event, source_id is in the path, HMAC is over the exact raw body,
    # headers are lowercase, this is not a FastAPI route
    started = time.monotonic()
    raw_path = event.get("rawPath") or ""
    parts = raw_path.strip("/").split("/")
    try:
        if len(parts) != 2 or parts[0] != "ingest":
            raise ValueError
        source_id = uuid.UUID(parts[1])
    except ValueError:
        return _lambda_error(400, "source_not_found", "Unknown or disabled source_id.")

    body = event.get("body")
    if body is None:
        raw_body = b""
    elif event.get("isBase64Encoded"):
        raw_body = base64.b64decode(body)
    elif isinstance(body, str):
        raw_body = body.encode("utf-8")
    else:
        raw_body = body

    # Reject cheap failures first, no Event row for these
    if len(raw_body) > 65536:
        return _lambda_error(413, "payload_too_large", "Body longer than 64 KiB.")

    headers = event.get("headers") or {}
    provider_delivery_id = headers.get("x-webhook-id")
    if provider_delivery_id is None or not str(provider_delivery_id).strip():
        return _lambda_error(400, "missing_delivery_id", "Missing or empty X-Webhook-Id.")
    provider_delivery_id = str(provider_delivery_id).strip()

    signature_header = headers.get("x-webhook-signature-256")
    if not signature_header:
        return _lambda_error(
            401, "signature_invalid", "Missing signature header or HMAC mismatch."
        )

    event_type = headers.get("x-webhook-event")
    content_type = headers.get("content-type")

    # Open Postgres, same database as Render, Lambda env, not FastAPI Settings
    settings = get_lambda_settings()
    with Session(get_engine()) as session:
        result = ingest(
            source_id,
            raw_body,
            provider_delivery_id,
            signature_header,
            event_type,
            content_type,
            session,
            settings,
        )
    if result.get("statusCode") == 202:
        emit_ms(
            "ingestion_ack_latency_ms",
            max(0, int((time.monotonic() - started) * 1000)),
        )
    return result


def ingest(
    source_id: uuid.UUID,
    raw_body: bytes,
    provider_delivery_id: str,
    signature_header: str,
    event_type: str | None,
    content_type: str | None,
    session: Session,
    settings: LambdaSettings,
) -> dict:
    # Find the source, missing or disabled is the same 400
    source = session.get(Source, source_id)
    if source is None or source.disabled_at is not None:
        return _lambda_error(400, "source_not_found", "Unknown or disabled source_id.")

    secret_row = session.get(SourceSecret, source_id)
    if secret_row is None:
        return _lambda_error(400, "source_not_found", "Unknown or disabled source_id.")

    # Verify HMAC, Fernet unwraps ciphertext, the string is the source secret the provider has
    plaintext = decrypt_secret(
        secret_row.ciphertext,
        key=settings.secret_encryption_key.get_secret_value(),
    )
    if not _signature_matches(plaintext, raw_body, signature_header):
        return _lambda_error(
            401, "signature_invalid", "HMAC does not match the raw body."
        )

    # Dedupe, same delivery id returns the first Event, never 409
    existing = session.scalars(
        select(Event).where(
            Event.source_id == source_id,
            Event.provider_delivery_id == provider_delivery_id,
        )
    ).one_or_none()
    if existing is not None:
        return _accepted_202(existing.id, existing.correlation_id)

    project = session.get(Project, source.project_id)
    if project is None:
        return _lambda_error(
            503, "worker_unhandled", "Validation passed but the durable write failed."
        )

    now = datetime.now(timezone.utc)
    year_month = date(now.year, now.month, 1)
    stored_event_type = event_type
    if stored_event_type is not None:
        stored_event_type = stored_event_type.strip()[:128] or None

    try:
        # Write once, together, quota + Event accepted + Delivery queued + OutboxJob pending
        # 202 only after this commit, write fail is 503
        retained = session.scalar(
            select(func.coalesce(func.sum(Event.byte_size), 0)).where(
                Event.status == "accepted"
            )
        )
        if retained + len(raw_body) > RETAINED_PAYLOAD_BUDGET_BYTES:
            session.rollback()
            return _lambda_error(
                429,
                "storage_admission_denied",
                "Retained-payload budget would be exceeded.",
            )

        user_count = session.execute(
            pg_insert(UsageMonthly)
            .values(
                user_id=project.user_id,
                year_month=year_month,
                accepted_count=1,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "year_month"],
                set_={
                    "accepted_count": UsageMonthly.accepted_count + 1,
                    "updated_at": now,
                },
            )
            .returning(UsageMonthly.accepted_count)
        ).scalar_one()
        if user_count > USER_MONTHLY_EVENT_CAP:
            session.rollback()
            return _lambda_error(429, "quota_exceeded", "Accepted-event quota exceeded.")

        global_count = session.execute(
            pg_insert(UsageGlobalMonthly)
            .values(
                year_month=year_month,
                accepted_count=1,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["year_month"],
                set_={
                    "accepted_count": UsageGlobalMonthly.accepted_count + 1,
                    "updated_at": now,
                },
            )
            .returning(UsageGlobalMonthly.accepted_count)
        ).scalar_one()
        if global_count > GLOBAL_MONTHLY_EVENT_CAP:
            session.rollback()
            return _lambda_error(429, "quota_exceeded", "Accepted-event quota exceeded.")

        event = Event(
            project_id=source.project_id,
            source_id=source.id,
            provider_delivery_id=provider_delivery_id,
            event_type=stored_event_type,
            content_type=content_type,
            body=raw_body,
            byte_size=len(raw_body),
            status="accepted",
            correlation_id=uuid.uuid4(),
            accepted_at=now,
        )
        session.add(event)
        session.flush()

        delivery = Delivery(
            project_id=source.project_id,
            event_id=event.id,
            destination_id=source.destination_id,
            status="queued",
            release_after=event.accepted_at,
            created_at=now,
            updated_at=now,
        )
        session.add(delivery)
        session.flush()

        outbox = OutboxJob(
            project_id=source.project_id,
            delivery_id=delivery.id,
            status="pending",
            release_after=delivery.release_after,
            created_at=now,
        )
        session.add(outbox)
        session.commit()
        emit_count("accepted_events_total")
    except IntegrityError:
        session.rollback()
        raced = session.scalars(
            select(Event).where(
                Event.source_id == source_id,
                Event.provider_delivery_id == provider_delivery_id,
            )
        ).one_or_none()
        if raced is not None:
            return _accepted_202(raced.id, raced.correlation_id)
        return _lambda_error(
            503, "worker_unhandled", "Validation passed but the durable write failed."
        )
    except SQLAlchemyError:
        session.rollback()
        return _lambda_error(
            503, "worker_unhandled", "Validation passed but the durable write failed."
        )

    # Then SQS, message is the OutboxJob id, not the payload, still 202 if send fails
    # reconciler republishes pending, ingest does not POST to the destination
    try:
        _send_outbox_message(str(settings.delivery_queue_url), outbox.id)
    except Exception as exc:
        failed = session.get(OutboxJob, outbox.id)
        if failed is not None:
            failed.last_error = str(exc)
            session.commit()
        return _accepted_202(event.id, event.correlation_id)

    enqueued = session.get(OutboxJob, outbox.id)
    if enqueued is not None:
        enqueued.status = "enqueued"
        enqueued.enqueued_at = datetime.now(timezone.utc)
        session.commit()
    return _accepted_202(event.id, event.correlation_id)


# Source is our record of one inbound webhook: ingest URL, source secret, which destination
# source_id is ours (minted on create), the provider never invents it, they POST to the URL we gave them
# what they do send is X-Webhook-Id, their delivery id, for dedupe
# you paste ingest_url and the source secret into the provider's webhook settings
# HMAC proves the sender still has that secret, anyone can hit the Function URL, a guess cannot make a valid signature
# they send HMAC of the raw body, not the secret, ingest never returns the secret (create/rotate, JWT only)
