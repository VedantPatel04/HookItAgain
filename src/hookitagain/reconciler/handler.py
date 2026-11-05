import uuid
from datetime import datetime, timedelta, timezone

import boto3
from sqlalchemy import select
from sqlalchemy.orm import Session

from hookitagain.db.models import OutboxJob
from hookitagain.db.session import get_engine
from hookitagain.settings import get_lambda_settings

ENQUEUE_GRACE_SECONDS = 30


def _send_outbox_message(queue_url: str, outbox_job_id: uuid.UUID) -> None:
    boto3.client("sqs").send_message(
        QueueUrl=queue_url,
        MessageBody=str(outbox_job_id),
    )


def handle(event, context):
    # pending OutboxJob past 30s grace, release_after due, SendMessage the job id
    # success: enqueued, duplicate SQS messages are allowed
    # send fail: leave pending, last_error, try the rest, no payload purge
    settings = get_lambda_settings()
    now = datetime.now(timezone.utc)
    grace_before = now - timedelta(seconds=ENQUEUE_GRACE_SECONDS)
    with Session(get_engine()) as session:
        jobs = list(
            session.scalars(
                select(OutboxJob)
                .where(
                    OutboxJob.status == "pending",
                    OutboxJob.release_after <= now,
                    OutboxJob.created_at <= grace_before,
                )
                .order_by(OutboxJob.created_at.asc())
            ).all()
        )
        for job in jobs:
            try:
                _send_outbox_message(str(settings.delivery_queue_url), job.id)
            except Exception as exc:
                job.last_error = str(exc)
                session.commit()
                continue
            job.status = "enqueued"
            job.enqueued_at = datetime.now(timezone.utc)
            job.last_error = None
            session.commit()
