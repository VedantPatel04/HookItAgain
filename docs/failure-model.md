# Failure model

This is the Sprint 1.1 exit-criteria table. For every critical break in the pipeline it names **who owns recovery**, **what is persisted**, **how retry happens**, and **how the path ends**.

An accepted Event must always be reconcilable against OutboxJob and Delivery state. The reliability target is **zero unaccounted accepted events** (see [metrics.md](metrics.md)).

Constants (timeouts, receive count, grace windows) live in [timeouts-quotas.md](timeouts-quotas.md). Error category strings live in [error-categories.md](error-categories.md).

## Pipeline

Provider POST → ingest Lambda → Postgres commit (Event + Delivery + OutboxJob) → `202` → SQS publish → worker Lambda → HTTPS destination or CLI relay → append DeliveryAttempt.

## Matrix

### Crash before ingest commit

- **Problem:** The ingest Lambda dies, the database is unreachable, or the transaction rolls back before commit. The provider has not received `202`.
- **Owner:** The provider (GitHub or generic sender).
- **Persisted state:** None. No Event, Delivery, or OutboxJob.
- **Retry:** The provider retries the POST.
- **Terminal:** Either a later POST commits and returns `202`, or the provider gives up. We never show a phantom event.

### Invalid signature, missing delivery id, oversize, quota, or storage admission

- **Problem:** The request is well-formed as HTTP but fails validation or admission.
- **Owner:** Ingest Lambda.
- **Persisted state:** None. Structured log only (`error_category`, `source_id` if known, `correlation_id`). Invalid signatures must not consume the retained-payload budget.
- **Retry:** None from us. HTTP `401` / `400` / `413` / `429` as defined in [api-contracts.md](api-contracts.md).
- **Terminal:** Rejection. The provider may retry; the same rejection applies until the request becomes valid or quota frees up.

### Database commit fails after validation

- **Problem:** Signature, size, and quotas passed, but the Event/Outbox transaction did not commit.
- **Owner:** Ingest Lambda returns `503`. Provider may retry.
- **Persisted state:** None (transaction rolled back).
- **Retry:** Provider retry. If a later attempt commits, that becomes the Event.
- **Terminal:** `202` on a later success, or the provider gives up. No accepted Event is created on `503`.

### Commit succeeded, SQS publish failed

- **Problem:** Postgres committed and we returned (or will return) `202`, but `SendMessage` failed or the Lambda crashed before it. Postgres and SQS cannot commit together.
- **Owner:** Outbox reconciler (scheduled Lambda).
- **Persisted state:** Event `accepted`, Delivery `queued`, OutboxJob `pending`.
- **Retry:** Reconciler finds `pending` jobs past the enqueue-grace window and calls `SendMessage` again.
- **Terminal:** OutboxJob `enqueued`. Delivery then follows the worker path. An accepted Event must not stay `pending` indefinitely; reconciler cadence is in [timeouts-quotas.md](timeouts-quotas.md).

### Worker crash before attempt insert

- **Problem:** The worker received the SQS message, possibly started HTTP, then died before inserting a DeliveryAttempt.
- **Owner:** SQS visibility timeout ([ADR 0003](adr/0003-retry-ownership.md)).
- **Persisted state:** Delivery may be `in_flight` (if the status update committed) or still `queued`. No attempt row.
- **Retry:** When the visibility timeout expires, SQS shows the **same message** again. The next receive is a new `attempt_number` (`ApproximateReceiveCount`).
- **Terminal:** A later attempt records success, or five receives send the message to the DLQ.

### Destination timeout, 4xx, 5xx, SSRF, redirect, or CLI disconnect

- **Problem:** The worker finished a try and the destination did not return 2xx, or the destination URL was blocked, or the CLI socket dropped.
- **Owner:** SQS visibility timeout and redrive policy. The worker does not loop.
- **Persisted state:** One append-only DeliveryAttempt with the matching `error_category`. Delivery returns to `queued` when `receive_count < 5`.
- **Retry:** The worker fails the invocation so the message is not deleted. SQS makes it visible again.
- **Terminal:** A later 2xx → Delivery `succeeded`; or five failed receives → DLQ and Delivery `dead_lettered`.
- **Note:** 4xx is retryable here on purpose. This is a failure lab; SQS owns a uniform five-attempt lifecycle. SSRF and redirect blocks count as failed attempts with `ssrf_blocked` / `redirect_blocked`.

### Succeeded, but SQS delete failed

- **Problem:** The destination returned 2xx, the attempt was recorded, Delivery is `succeeded`, then `DeleteMessage` failed or the worker crashed.
- **Owner:** Worker, on the next receive.
- **Persisted state:** Delivery `succeeded`, successful DeliveryAttempt already present.
- **Retry:** SQS redelivers the same message. The worker sees `succeeded`, does **not** POST to the destination, and deletes the message.
- **Terminal:** Message deleted. At-least-once delivery may still POST twice if the crash happened *after* HTTP succeeded and *before* the attempt/status commit; downstream consumers remain responsible for their own idempotency.

### Five receives

- **Problem:** Automatic delivery has failed five times.
- **Owner:** SQS redrive policy (`maxReceiveCount = 5`).
- **Persisted state:** Five DeliveryAttempt rows (or fewer if some receives crashed before insert). On the fifth failed receive the worker marks Delivery `dead_lettered`. A DLQ consumer is allowed to set `dead_lettered` idempotently if the worker could not.
- **Retry:** None automatic.
- **Terminal:** Message on the DLQ. Dashboard history remains. Human recovery is replay, not editing the DLQ.

### Replay after DLQ (or any replay)

- **Problem:** The operator wants another dispatch without erasing why the last one failed.
- **Owner:** Control API `POST /api/deliveries/{id}/replay`.
- **Persisted state:** New Delivery (`queued`) with `replayed_from_delivery_id`, new OutboxJob `pending`. Prior Delivery and attempts are untouched.
- **Retry:** The new Delivery has its own five SQS receives.
- **Terminal:** New Delivery `succeeded` or `dead_lettered`. Old rows stay append-only history.

### Duplicate scheduled suite

- **Problem:** EventBridge (or a client) fires twice for the same suite at the same planned time.
- **Owner:** Database unique constraint `(suite_id, scheduled_for)`.
- **Persisted state:** One SuiteRun. The loser of the insert receives a uniqueness error and does not expand jobs.
- **Retry:** None needed; the first run is the run.
- **Terminal:** One SuiteRun in `scheduled` then `running` then `completed` or `failed`.

### Render idle or WebSocket drop

- **Problem:** The free FastAPI service spins down or restarts, or the network drops, so the CLI’s relay socket dies mid-delivery.
- **Owner:** CLI reconnect with backoff ([ADR 0005](adr/0005-websocket-relay.md)); SQS for the in-flight attempt.
- **Persisted state:** If a delivery was in flight, the worker records `cli_disconnected` or `cli_timeout` as a failed attempt (or SQS redelivers if the worker crashed).
- **Retry:** CLI reconnects and authenticates with a (possibly refreshed) relay token. SQS will redeliver the attempt. The CLI suppresses a second localhost POST for the same `delivery_attempt_id` / `attempt_id`.
- **Terminal:** Localhost eventually returns a response that the dashboard stores, or the Delivery reaches the DLQ like any other failed destination. There is no uptime SLA on Render free.

## Ownership summary

| Class of failure                         | Owner                         |
| ---------------------------------------- | ----------------------------- |
| Never committed                          | Provider retry                |
| Validation / quota rejection             | Ingest Lambda (no row)        |
| Committed but not on SQS                 | Outbox reconciler             |
| Worker or destination failure            | SQS visibility + redrive      |
| Auto-delivery exhausted                  | DLQ; human replay             |
| Double scheduler fire                    | Unique constraint             |
| Relay process gone                       | CLI reconnect + SQS retry     |
