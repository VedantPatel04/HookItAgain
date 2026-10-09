# Error categories

Closed enum. Store these strings on DeliveryAttempt.`error_category`, in structured logs, and as `error.code` on ingest rejections. Do not add values in feature PRs without updating this file and the database CHECK.

## Ingest (no Event row)

Used on the ingest Lambda response and in logs only.

| Value | HTTP | Meaning |
| ----- | ---- | ------- |
| `missing_delivery_id` | 400 | Generic source omitted `X-Webhook-Id`, or GitHub omitted `X-GitHub-Delivery`. |
| `source_not_found` | 400 | Path `source_id` does not exist or is disabled. |
| `signature_invalid` | 401 | Missing signature header or HMAC does not match the unmodified body. |
| `payload_too_large` | 413 | Body longer than 64 KiB. |
| `quota_exceeded` | 429 | User monthly or global accepted-event quota. |
| `storage_admission_denied` | 429 | Retained-payload budget would be exceeded. |

`503` on a failed durable write after validation uses `worker_unhandled` in logs (the client-facing code is still a generic 503; the provider retries).

## Attempts (DeliveryAttempt rows)

Null `error_category` means the attempt succeeded (2xx). Otherwise exactly one of:

| Value | Meaning |
| ----- | ------- |
| `destination_timeout` | HTTP client hit the 10s delivery timeout. |
| `destination_connect_error` | DNS, TLS, or TCP failure before an HTTP response. |
| `destination_4xx` | HTTP 400–499 from the destination (except we still classify 408 as timeout if we timed out first). |
| `destination_5xx` | HTTP 500–599 from the destination. |
| `redirect_blocked` | Destination responded with a redirect; we do not follow. |
| `ssrf_blocked` | URL or resolved address was private, loopback, link-local, reserved, or not HTTPS. |
| `cli_disconnected` | Relay socket was down when the worker tried to forward, or it dropped mid-attempt. |
| `cli_timeout` | CLI / localhost did not answer within the delivery HTTP timeout. |
| `worker_unhandled` | Unexpected exception in the worker after receive. Still counts as a failed attempt; SQS retries. |

## Control plane (not stored on attempts)

`unauthorized`, `forbidden`, `not_found`, `validation_error`, `conflict` — JSON `error.code` only ([api-contracts.md](api-contracts.md)).

## Mapping rules

- Prefer the most specific category. Example: a private IP is `ssrf_blocked`, not `destination_connect_error`.
- CLI destinations never use `ssrf_blocked` for localhost; localhost is the point. They use `cli_*`.
- Ingest rejects never become DeliveryAttempts.
