# ADR 0002 — Idempotency keys

## Status

Accepted

## Context

GitHub and other providers retry POSTs when they do not see a success status, and they may also send the same delivery more than once under load. If we inserted a new Event for each POST, the dashboard would show duplicates and we would dispatch the destination multiple times for one provider delivery. If we returned `409 Conflict` for a retry, many providers would keep retrying because they treat anything other than 2xx as failure.

## Decision

- The idempotency key for ingest is **`(source_id, provider_delivery_id)`**, enforced as a unique constraint.
- GitHub sources read `X-GitHub-Delivery`. Generic HMAC sources require `X-Webhook-Id`. Missing id is `400` with `missing_delivery_id`; it is not stored.
- A second POST with the same key returns **`202`** and the original `event_id` and `correlation_id`. It does not insert a second Event, Delivery, or OutboxJob.
- Concurrent inserts: one transaction wins; the loser catches the unique-violation and loads the existing Event. Both return `202`.
- Manual replay and suite expansion are **not** ingest duplicates. They insert new Delivery rows (and new OutboxJobs) linked to the existing Event.

Rejected ingestions (bad signature, oversize, quota) are not persisted, so they have no idempotency row. A later valid POST with the same delivery id is the first Event.

## Consequences

- Retry from the provider is indistinguishable from a duplicate, and that is intended: one Event, one initial Delivery.
- Generic sources that omit `X-Webhook-Id` cannot be deduplicated; we refuse them rather than accepting silently.
- Downstream handlers must still be idempotent. We promise at-least-once **delivery**, not exactly-once.
