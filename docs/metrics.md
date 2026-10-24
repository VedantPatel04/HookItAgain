# Engineering metrics

Correctness, reliability, idempotency, and security are requirements. Speed, throughput, recovery, and efficiency are optimized only after those hold.

For any change that could move a number below, record **baseline → change → measured result** under the same conditions in [metrics/baselines.md](metrics/baselines.md). These are measurements of the system under test, not product SLAs. Published limits stay in [timeouts-quotas.md](timeouts-quotas.md).

## Always-on instrumentation

Emit from ingest, worker, reconciler, and (where noted) CloudWatch/SQS. Metric names are exact.

| Name | Unit | Definition | How to test |
| ---- | ---- | ---------- | ----------- |
| `accepted_events_total` | count | Event rows successfully committed (`status` accepted or later purged). Increment once per new Event, not on provider retries of the same key. | Ingest N unique ids; value increases by N. Repeat one id; value does not increase. |
| `unaccounted_events_total` | count | `accepted_events` minus events that still have an OutboxJob `pending` **or** a Delivery in `queued` / `in_flight` / `succeeded` / `dead_lettered`. Target **0**. | After failure injection (DB, enqueue, worker), reconcile. Any leftover is a bug. |
| `queue_age_seconds` | seconds | SQS approximate age of the oldest visible message on the delivery queue. | Enqueue then pause the worker; age should rise. Resume; age should fall. |
| `delivery_success_ratio` | ratio | In a time window: deliveries `succeeded` / (`succeeded` + `dead_lettered`). Exclude still-open (`queued`, `in_flight`). | Healthy receiver → ~1. Forced 5xx until DLQ → 0 for those ids. |
| `retry_attempts_total` | count | DeliveryAttempt inserts with `attempt_number > 1`. | Fail the receiver once then succeed; this counter increases by 1 per recovered delivery. |
| `dlq_depth` | count | Approximate number of messages on the DLQ. | Five failed receives; depth increases by 1 per exhausted delivery. Replay does not drain DLQ (we do not auto-redrive). |
| `ingestion_ack_latency_ms` | milliseconds | Clock at request arrival (Lambda start) to `202` after commit. Include signature verify and DB time; log those two as dimensions/subtimers when possible. | See baseline protocol. |
| `delivery_latency_ms` | milliseconds | Event `accepted_at` to the `completed_at` of the **first successful** attempt (2xx). Queue wait is included. Also log `delivery_http_ms` (attempt `duration_ms`) so queue wait can be separated. | See baseline protocol. |

## Evaluation metrics (named now, measured in Sprint 2 capacity work)

| Name | Unit | Definition | How to test |
| ---- | ---- | ---------- | ----------- |
| `recovery_percent` | percent | Of deliveries that were failing when the destination was restored, the share that reached `succeeded` before hitting the DLQ, inside a fixed window (lock **120 seconds** from restore). | Take the destination down, send N events, restore while retries remain, wait 120s. |
| `attempts_per_recovered_event` | count | Mean `attempt_number` of the successful attempt for those recovered deliveries. | Same experiment. Lower is better only if `recovery_percent` does not fall. |
| `sustainable_events_per_second` | events/s | Highest constant ingest rate at which `queue_age_seconds` stays bounded and no accepted event is unaccounted. | Increase rate until backlog grows or ack latency explodes; report the last stable rate. |
| `burst_drain_seconds` | seconds | Time from enqueue of a fixed burst (lock **1 000** events of the 1024-byte payload) until queue empty and all deliveries terminal. | Pause worker, load burst, start worker, time to drain. |
| `queries_per_ingest` | count | SQL statements per accepted ingest (including usage/budget). | Trace one ingest. |
| `queries_per_event_detail` | count | SQL statements for `GET /api/events/{id}` (detail screen). | Trace one request. |
| `compute_ms_per_delivered_event` | milliseconds | Ingest + worker billed duration / unique succeeded deliveries for a workload. | Same workload before and after a change. |
| `queue_requests_per_delivered_event` | count | SQS send/receive/delete counts / unique succeeded deliveries. | CloudWatch vs delivery count. |
| `stored_bytes_per_delivered_event` | bytes | Increase in retained payload + attempt excerpts / unique succeeded deliveries. | Measure DB before/after. |

## Baseline protocol (lock now, run in Sprint 1 section 3)

First vertical-slice numbers use this protocol so later PRs compare like-for-like.

**Payload.** JSON padded to **1024 bytes**. The 64 KiB maximum is a *boundary* test, not the latency baseline.

**Receiver.** Controlled staging HTTPS endpoint that returns `200` in well under the 10s timeout, with no injected faults.

**Load.** Sequential **N = 100** unique ingest → deliver runs (not concurrent). Same AWS region as the Lambdas.

**Report.** p50 and p95 of `ingestion_ack_latency_ms` and `delivery_latency_ms`. Also record `accepted_events_total` (100), `unaccounted_events_total` (0), and that every delivery is `succeeded`.

**Environment block** (copy into the baseline log): stack name, region, ingest/worker memory, reserved concurrency, DB pooling, receiver URL (staging, not a third-party).

**Template.** Each row in [metrics/baselines.md](metrics/baselines.md): date, git SHA, environment, metric, baseline, change (or “initial”), measured result, conditions (this protocol unless stated).

Do not mark a performance story complete on anecdote. If the protocol cannot be followed (for example no staging receiver yet), leave the row empty rather than filling it with a local guess.
