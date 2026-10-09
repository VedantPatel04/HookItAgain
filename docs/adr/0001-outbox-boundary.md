# ADR 0001 — Outbox boundary

## Status

Accepted

## Context

Ingest must acknowledge a webhook only after the event is durable. The ingest Lambda cannot commit Postgres and publish to SQS in one atomic step. If we publish first and then crash before commit, we may deliver an event we never stored. If we commit, return `202`, and then crash before publish, we have accepted an event the worker will never see unless something else publishes it.

## Decision

1. In one Postgres transaction, insert the Event, the initial Delivery (`queued`), and an OutboxJob (`pending`) that references that delivery. Return `202 Accepted` only after that commit.
2. After commit, publish the **OutboxJob id** (not the payload) to SQS with `SendMessage`. On success, mark the OutboxJob `enqueued`.
3. A scheduled reconciler republishes any `pending` job that is past the enqueue-grace window and whose `release_after` is due. Reconcile is idempotent: duplicate SQS messages are acceptable because delivery is at-least-once and the worker is keyed by delivery id.

The outbox is finished at `enqueued`. Visibility timeout, retries, and the DLQ are SQS’s problem after that ([ADR 0003](0003-retry-ownership.md)).

## Consequences

- An accepted Event cannot exist without an OutboxJob row, so “accepted but vanished” is detectable.
- Enqueue is eventually consistent with the table. The provider may see `202` a short time before the message is on SQS.
- The reconciler is a reliability requirement, not an optional cleanup job.
- We never acknowledge before commit. A crash before commit looks like a provider-visible failure and they retry.
