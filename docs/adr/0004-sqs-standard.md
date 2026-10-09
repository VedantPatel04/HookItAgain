# ADR 0004 — SQS Standard, not FIFO

## Status

Accepted

## Context

The lab must reproduce duplicates, delay, and out-of-order delivery. SQS FIFO would advertise ordering and exactly-once processing that we do not want to promise, and it would fight the product: handlers need to see unordered, at-least-once behavior.

SQS Standard does not preserve order. Deduplication is not built in. That matches “delivery is at-least-once.”

## Decision

- Use **SQS Standard** for the delivery queue and a Standard DLQ. Batch size is one.
- Do not rely on enqueue order for scenario ordering.
- Duplicate, delay, and reordering suites expand into **separate Delivery rows** with explicit `release_after` timestamps. The reconciler/orchestrator publishes an OutboxJob only when `release_after <= now()`.
- SQS `DelaySeconds` is not the ordering mechanism (it is capped at 15 minutes and is easy to confuse with product-level delay).

## Consequences

- Two jobs published in order A then B may be received as B then A. Tests that need A-before-B must set `release_after` so A becomes eligible first.
- Duplicate-suite cases are two Deliveries (two OutboxJobs), not one message with a “send twice” flag.
- FIFO features (content-based deduplication, message groups) will not be added in the MVP.
