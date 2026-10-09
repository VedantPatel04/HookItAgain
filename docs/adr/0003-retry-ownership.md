# ADR 0003 — Retry ownership

## Status

Accepted

## Context

Destination failures are the product: timeouts, 5xx, downtime, CLI disconnects. If the worker implements its own sleep-and-retry loop, we duplicate what SQS already provides, we risk retrying after a process crash anyway, and we make “exactly five tries” a heuristic instead of a queue policy.

SQS Standard can redeliver. Combined with a visibility timeout and a redrive policy, receive count *is* the attempt lifecycle.

## Decision

- **SQS owns automatic retries.** Visibility timeout hides a message while a worker runs. If the worker deletes the message, the try is done. If the worker fails or dies, the timeout expires and SQS shows the **same message** again.
- **`maxReceiveCount` is 5**, then the message moves to the DLQ. The worker does not catch failures and call `SendMessage` itself.
- The worker records **one append-only DeliveryAttempt per receive**, using `ApproximateReceiveCount` as `attempt_number`, then:
  - 2xx: mark Delivery `succeeded`, delete the message.
  - otherwise: mark Delivery `queued` (or `dead_lettered` on the fifth failed receive), then fail the invocation so the message is not deleted.
- Any non-2xx, timeout, connect error, SSRF/redirect block, or CLI disconnect is retryable until the DLQ. We do not special-case 4xx for auto-retry (this is a failure lab with a uniform five-attempt policy).
- If Delivery is already `succeeded` on a later receive, skip HTTP and delete the message.
- Manual replay is a new Delivery with its own five receives. Prior attempts are never updated.

Worker timeout must be **shorter** than visibility timeout so a live worker cannot overlap a redelivery of the same message. Values are in [timeouts-quotas.md](../timeouts-quotas.md).

## Consequences

- “How many times did we try?” is `ApproximateReceiveCount` aligned with attempt rows, not an application counter.
- A crash before attempt insert produces a gap (receive 2 exists, receive 1 might not). That is accepted; SQS is still the source of retry truth.
- Downstream consumers can see up to five automatic POSTs plus any replays. They must be idempotent.
- DLQ is the terminal bucket for auto-delivery. Operators replay from the API; they do not redrive the DLQ as the normal recovery path.
