# FAQ — Dev

Answers tied to `webhook_failure_lab_refined_plan.md`.

## What is a webhook handler?

The downstream consumer of a webhook: the user's HTTPS endpoint or local CLI process that receives the payload and returns a response.

This lab is not the handler. It sits in front of it, captures events, delivers them (including failures/retries), and records every attempt so the handler can be tested under messy delivery.

## What is an ingestion Lambda, ingestion URL, source secret?

**Ingestion Lambda** — Python AWS Lambda with a Function URL. It is the public receive path: validate size/quotas, verify signature on the raw body, dedupe, then persist event + outbox job and return `202 Accepted`.

**Ingestion URL** — That Function URL, unique per webhook source. The provider (GitHub or generic) POSTs events here.

**Source secret** — Shared HMAC key for that source, stored encrypted as `SourceSecret`. The provider signs with it, Lambda verifies with it. Outgoing deliveries use a fresh timestamped HMAC, not this inbound secret reused as-is.

## What is the event provided to the ingestion Lambda?

A provider HTTP POST: headers + unmodified body (max 64 KB).

Identity for dedupe:

- GitHub: `X-GitHub-Delivery`
- Generic: `X-Webhook-Id`

Dedupe key is `(source_id, provider_delivery_id)`. After commit, that request becomes an `Event` row. The Lambda does not deliver to the destination itself.

## What signature is used for verification by Lambda?

HMAC over the **exact raw request body** (never a re-serialized JSON object).

- **GitHub source:** GitHub's signature header (`X-Hub-Signature-256`) vs the source secret
- **Generic HMAC source:** the same idea — HMAC of the unmodified body vs the source secret

Mismatch → reject. Do not parse/normalize the body before hashing.

## What is an outbox job?

An **outbox** is a table of “work still to enqueue.” You cannot commit Postgres and publish SQS atomically, so a crash between those two steps can lose a delivery. The transactional outbox pattern stores the intent in the database first, then publishes.

Here that row is `OutboxJob`, written in the **same DB transaction** as the `Event`. `202` is returned only after that commit. The request then puts the job ID on SQS. If that publish fails, a scheduled reconciler finds committed jobs that were never enqueued and publishes them. The event is durable before ack. The queue is eventually consistent with the table.

## What are we enqueuing?

The **outbox job ID** (not the full payload) onto an SQS Standard queue, one message at a time.

A worker Lambda dequeues that ID, loads event/destination from Postgres, and delivers. Scenario suites enqueue extra delivery jobs with explicit release times.

## What is a DLQ?

**Dead-letter queue** — a second SQS queue for jobs that fail delivery.

SQS visibility timeout + redrive policy own retries. After **five receives**, the message moves to the DLQ. The worker does not implement a separate retry loop. Dashboard/history still show every attempt; DLQ is the terminal “gave up automatic delivery” bucket.