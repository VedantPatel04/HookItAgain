"""
Locked numerical values as discussed in my Sprint 1 and 2.
Values are from docs/timeouts-quotas.md.
CDK constructs import these so as a reminder to myself: Do not change them without a measured baseline comparison.
Lambda memory is only proposed in that doc (256 MB).
"""

INGEST_BODY_MAX_BYTES = 65536
DELIVERY_HTTP_TIMEOUT_SECONDS = 10
WORKER_LAMBDA_TIMEOUT_SECONDS = 20
SQS_VISIBILITY_TIMEOUT_SECONDS = 30
SQS_MAX_RECEIVE_COUNT = 5
SQS_BATCH_SIZE = 1
RESPONSE_EXCERPT_BYTES = 2048

ENQUEUE_GRACE_SECONDS = 30
RECONCILER_SCHEDULE_SECONDS = 60
PAYLOAD_RETENTION_DAYS = 7

INGEST_RESERVED_CONCURRENCY = 5
WORKER_RESERVED_CONCURRENCY = 2
RECONCILER_RESERVED_CONCURRENCY = 1
SUITE_ORCHESTRATOR_RESERVED_CONCURRENCY = 1
LAMBDA_POOL_SIZE = 1
LAMBDA_MAX_OVERFLOW = 0

# Proposed in docs/timeouts-quotas.md until a baseline comparison exists.
INGEST_WORKER_MEMORY_MB = 256
