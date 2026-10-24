# Webhook Failure Lab — contracts

This directory is the Sprint 1.1 contract set. Later code (FastAPI, Lambdas, CLI, CDK, Alembic) should implement these files rather than inventing statuses, status codes, timeouts, or metrics.

Product intent, architecture, and operating boundaries live in [`webhook_failure_lab_refined_plan.md`](../webhook_failure_lab_refined_plan.md). Term definitions live in [`faq-dev.md`](../faq-dev.md). Numbers and enums are defined once in the catalogs below; ADRs record *why*, not the duplicated constants.

## Read in this order

1. [State machines](state-machines.md) — Event, OutboxJob, Delivery, DeliveryAttempt, SuiteRun
2. [Failure model](failure-model.md) — every critical break: owner, persisted state, retry, terminal outcome
3. ADRs
   - [0001 — Outbox boundary](adr/0001-outbox-boundary.md)
   - [0002 — Idempotency keys](adr/0002-idempotency-keys.md)
   - [0003 — Retry ownership](adr/0003-retry-ownership.md)
   - [0004 — SQS Standard](adr/0004-sqs-standard.md)
   - [0005 — WebSocket relay](adr/0005-websocket-relay.md)
4. [API contracts](api-contracts.md)
5. [Secrets and HMAC](secrets.md)
6. [Database constraints](database.md)
7. [Timeouts and quotas](timeouts-quotas.md)
8. [Error categories](error-categories.md)
9. [Engineering metrics](metrics.md) and the [baseline log](metrics/baselines.md)

## What this pass does not include

No application scaffolding, CI, CDK, or migrations. Those are Sprint 1 section 2, after this contract set is reviewable.

## Exit criteria

- Every failure point in [failure-model.md](failure-model.md) names an owner, persisted state, retry behavior, and terminal outcome.
- Every metric in [metrics.md](metrics.md) has a name, unit, and test method so later pull requests can compare like-for-like.
- The five required ADR topics are recorded.
- Section 2 can be implemented without inventing a status, HTTP status code, or timeout.
