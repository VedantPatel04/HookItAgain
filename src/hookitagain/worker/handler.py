import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from hookitagain.api.ssrf import https_destination_url_rejected
from hookitagain.crypto import decrypt_secret
from hookitagain.db.models import Delivery, DeliveryAttempt, Destination, Event, OutboxJob
from hookitagain.db.session import get_engine
from hookitagain.metrics import emit_ms
from hookitagain.settings import LambdaSettings, get_lambda_settings

DELIVERY_HTTP_TIMEOUT_SECONDS = 10
RESPONSE_EXCERPT_MAX_CHARS = 2048
MAX_RECEIVE_COUNT = 5


class DeliveryRetry(Exception):
    # raise after a failed attempt so Lambda does not delete the SQS message
    pass


def _outbound_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    # outbound HMAC is {timestamp}.{raw_body}, not the raw body alone (that is inbound)
    material = f"{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _excerpt(value: str | bytes | None) -> str | None:
    # DeliveryAttempt.response_excerpt, max 2048 chars
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    if text == "":
        return None
    return text[:RESPONSE_EXCERPT_MAX_CHARS]


def _status_after_failed_attempt(attempt_number: int) -> str:
    # fifth failed receive is dead_lettered, earlier ones go back to queued
    if attempt_number >= MAX_RECEIVE_COUNT:
        return "dead_lettered"
    return "queued"


def _error_category_for_status(http_status: int) -> str:
    # 2xx is success, caller should not use this, redirect is blocked, 4xx still retries
    if 300 <= http_status < 400:
        return "redirect_blocked"
    if 400 <= http_status < 500:
        return "destination_4xx"
    if 500 <= http_status < 600:
        return "destination_5xx"
    return "worker_unhandled"


def _post_to_destination(
    url: str,
    raw_body: bytes,
    content_type: str | None,
    timestamp: str,
    signature: str,
    correlation_id: uuid.UUID,
    event_id: uuid.UUID,
    delivery_id: uuid.UUID,
) -> httpx.Response:
    # locked outgoing POST, 10s, no redirects, Event body unchanged
    return httpx.post(
        url,
        content=raw_body,
        headers={
            "Content-Type": content_type or "application/octet-stream",
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature-256": signature,
            "X-Correlation-Id": str(correlation_id),
            "X-Webhook-Event-Id": str(event_id),
            "X-Webhook-Delivery-Id": str(delivery_id),
        },
        timeout=DELIVERY_HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    )


def handle(event, context):
    # SQS Records[0] body is the OutboxJob id
    # ApproximateReceiveCount is attempt_number (1-5)
    # get_lambda_settings and Session(get_engine), not get_settings / get_session
    # 2xx or already succeeded: return so Lambda deletes the message
    # anything else: raise so SQS retries, do not SendMessage yourself
    records = event.get("Records")
    if not records:
        raise DeliveryRetry("missing SQS record")

    record = records[0]
    body = record.get("body")
    try:
        outbox_job_id = uuid.UUID(str(body).strip())
    except ValueError:
        raise DeliveryRetry("invalid outbox job id")

    receive_count = None
    attrs = record.get("attributes")
    if attrs is not None:
        receive_count = attrs.get("ApproximateReceiveCount")
    try:
        attempt_number = int(receive_count)
    except (TypeError, ValueError):
        attempt_number = 1
    if attempt_number < 1:
        attempt_number = 1
    if attempt_number > MAX_RECEIVE_COUNT:
        attempt_number = MAX_RECEIVE_COUNT

    settings = get_lambda_settings()
    with Session(get_engine()) as session:
        deliver(outbox_job_id, attempt_number, session, settings)


def deliver(
    outbox_job_id: uuid.UUID,
    attempt_number: int,
    session: Session,
    settings: LambdaSettings,
) -> None:
    outbox = session.get(OutboxJob, outbox_job_id)
    if outbox is None:
        raise DeliveryRetry("outbox job not found")

    delivery = session.get(Delivery, outbox.delivery_id)
    if delivery is None:
        raise DeliveryRetry("delivery not found")

    event = session.get(Event, delivery.event_id)
    if event is None:
        raise DeliveryRetry("event not found")

    destination = session.get(Destination, delivery.destination_id)
    if destination is None:
        raise DeliveryRetry("destination not found")

    if delivery.status == "succeeded":
        return

    delivery.status = "in_flight"
    session.commit()

    blocked = (
        destination.url is None
        or https_destination_url_rejected(destination.url) is not None
    )
    if blocked:
        now = datetime.now(timezone.utc)
        session.add(
            DeliveryAttempt(
                project_id=delivery.project_id,
                delivery_id=delivery.id,
                attempt_number=attempt_number,
                http_status=None,
                duration_ms=0,
                error_category="ssrf_blocked",
                correlation_id=event.correlation_id,
                completed_at=now,
            )
        )
        delivery.status = _status_after_failed_attempt(attempt_number)
        delivery.updated_at = now
        session.commit()
        emit_ms("delivery_http_ms", 0)
        raise DeliveryRetry("destination URL is blocked")

    plaintext = decrypt_secret(
        destination.signing_secret_ciphertext,
        key=settings.secret_encryption_key.get_secret_value(),
    )

    raw_body = event.body if event.body is not None else b""
    timestamp = str(int(time.time()))
    signature = _outbound_signature(plaintext, timestamp, raw_body)
    started = time.monotonic()
    http_status: int | None = None
    error_category: str | None = None
    excerpt: str | None = None
    url = destination.url
    if url is None:
        raise DeliveryRetry("destination URL is blocked")
    try:
        response = _post_to_destination(
            url,
            raw_body,
            event.content_type,
            timestamp,
            signature,
            event.correlation_id,
            event.id,
            delivery.id,
        )
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        http_status = response.status_code
        excerpt = _excerpt(response.content)
        if 200 <= http_status <= 299:
            error_category = None
        else:
            error_category = _error_category_for_status(http_status)
    except httpx.TimeoutException:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        error_category = "destination_timeout"
    except httpx.RequestError:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        error_category = "destination_connect_error"
    except Exception:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        error_category = "worker_unhandled"

    now = datetime.now(timezone.utc)
    session.add(
        DeliveryAttempt(
            project_id=delivery.project_id,
            delivery_id=delivery.id,
            attempt_number=attempt_number,
            http_status=http_status,
            duration_ms=duration_ms,
            error_category=error_category,
            response_excerpt=excerpt,
            correlation_id=event.correlation_id,
            completed_at=now,
        )
    )
    if error_category is None:
        delivery.status = "succeeded"
        delivery.updated_at = now
        session.commit()
        emit_ms("delivery_http_ms", duration_ms)
        accepted_at = event.accepted_at
        if accepted_at.tzinfo is None:
            accepted_at = accepted_at.replace(tzinfo=timezone.utc)
        emit_ms(
            "delivery_latency_ms",
            max(0, int((now - accepted_at).total_seconds() * 1000)),
        )
        return

    delivery.status = _status_after_failed_attempt(attempt_number)
    delivery.updated_at = now
    session.commit()
    emit_ms("delivery_http_ms", duration_ms)
    raise DeliveryRetry(error_category)
