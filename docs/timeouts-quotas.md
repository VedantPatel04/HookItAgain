# Timeouts and quotas

These numbers are locked for Sprint 1–2. Change them only with a measured baseline comparison ([metrics.md](metrics.md)). ADRs refer here instead of repeating values.

## Request and delivery

| Name | Value | Why |
| ---- | ----- | --- |
| Ingest body maximum | `65536` bytes (64 KiB) | Operating boundary. Measured separately from the 1024-byte latency baseline. |
| Delivery HTTP timeout | 10 seconds | Bound the worker; destinations that hang must fail as `destination_timeout`. |
| Redirects | Disabled | Prevents SSRF via `Location` headers. |
| Worker Lambda timeout | 20 seconds | Must exceed HTTP timeout plus DB writes; must be **less than** visibility timeout. |
| SQS visibility timeout | 30 seconds | Lock so two workers do not run the same message at once. Longer than the worker timeout. |
| SQS `maxReceiveCount` | 5 | Then redrive to the DLQ. Attempt numbers are 1–5. |
| SQS batch size | 1 | One job per invocation; fail/delete maps to a single message. |
| SQS `DelaySeconds` | Not used for scenarios | Ordering uses `release_after` on OutboxJob ([ADR 0004](adr/0004-sqs-standard.md)). |
| Response excerpt | 2048 bytes | Stored on each attempt. |

## Outbox and scheduling

| Name | Value | Why |
| ---- | ----- | --- |
| Enqueue-grace window | 30 seconds | Reconciler ignores very new `pending` rows so it does not race the ingest Lambda’s own `SendMessage`. |
| Reconciler schedule | every 60 seconds | EventBridge. Also a place to run payload purge. |
| Payload retention | 7 days | Then `Event.body` is nulled and status becomes `payload_purged`. |

## Relay and CLI

| Name | Value | Why |
| ---- | ----- | --- |
| Relay token TTL | 15 minutes | Short-lived; CLI refreshes via the control API. |
| WebSocket heartbeat | 20 seconds | Server ping interval. |
| Relay silence disconnect | 45 seconds | No pong → session dead; in-flight attempts fail as `cli_timeout` / `cli_disconnected`. |
| CLI reconnect backoff | 1s, 2s, 4s, … cap 30s | Render free idle/restart is expected. |

## Admission quotas

| Name | Value |
| ---- | ----- |
| Demonstration users | 20 |
| Accepted events per user per UTC month | 500 |
| Accepted events globally per UTC month | 10 000 |
| Retained-payload budget | **209_715_200 bytes (200 MiB)** |

The 200 MiB budget is `SUM(events.byte_size)` for `status = 'accepted'` only. It leaves headroom inside the 500 MB Supabase Free database for metadata, indexes, Auth, and other tables. Ingest returns `429` / `quota_exceeded` when a user or global count would pass its limit, and `429` / `storage_admission_denied` when `current_retained_bytes + new_byte_size > 209_715_200`.

Rejected ingestions do not count toward event quotas or the byte budget.

## Concurrency caps (protect Postgres)

Serverless functions must not stampede the free database.

| Resource | Cap |
| -------- | --- |
| Ingest Lambda reserved concurrency | 5 |
| Worker Lambda reserved concurrency | 2 |
| Reconciler reserved concurrency | 1 |
| Suite orchestrator reserved concurrency | 1 |
| Connections per Lambda process | 1 (`pool_size=1`, `max_overflow=0`) through Supavisor |

## Environment (record beside metrics)

Baseline runs must note: AWS region, Lambda memory (propose 256 MB ingest / 256 MB worker until measured otherwise), stack name (`dev` vs `demo`), and whether the receiver was the controlled staging endpoint.
