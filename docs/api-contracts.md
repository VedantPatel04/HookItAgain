# API contracts

These are wire contracts, not framework code. FastAPI and the ingest Lambda must match the paths, headers, status codes, and JSON shapes below.

Unless noted, JSON object keys are snake_case. UUIDs are lowercase hyphenated strings. Timestamps are RFC 3339 UTC.

Shared error body:

```json
{
  "error": {
    "code": "signature_invalid",
    "message": "HMAC does not match the raw body."
  }
}
```

`error.code` is an [error category](error-categories.md) or an auth/control-plane code listed with each route. Do not invent additional success envelopes.

---

## 1. Ingestion (Lambda Function URL)

Unauthenticated except HMAC of the **exact raw body**. One Lambda, path distinguishes the source.

`POST /ingest/{source_id}`

Maximum body: 64 KiB (`65536` bytes). Do not parse JSON before verifying the signature.

### Generic HMAC source

Required headers:

| Header | Value |
| ------ | ----- |
| `X-Webhook-Id` | Provider delivery id (idempotency key). Non-empty, max 128 chars. |
| `X-Webhook-Signature-256` | `sha256=` + lowercase hex HMAC-SHA256 of the raw body using the source secret. |

Optional: `X-Webhook-Event` (copied to `Event.event_type`, max 128 chars), `Content-Type`.

### GitHub source (contract now; implementation in Sprint 2)

| Header | Value |
| ------ | ----- |
| `X-GitHub-Delivery` | Provider delivery id. |
| `X-Hub-Signature-256` | GitHub’s `sha256=<hex>` over the raw body. |
| `X-GitHub-Event` | Copied to `Event.event_type`. |

### Success

`202 Accepted`

```json
{
  "event_id": "3d1f0c6a-8b2e-4a91-9c11-0c9e6b1a7f20",
  "correlation_id": "b7a0e2d1-4c33-4f0a-9a18-2c8d1e4f5a60"
}
```

A retry with the same `(source_id, provider_delivery_id)` returns the **same** body and `202`. Never `409`.

### Errors (no Event row)

| Status | `error.code` | When |
| ------ | ------------ | ---- |
| `400` | `missing_delivery_id` | Missing/empty `X-Webhook-Id` or `X-GitHub-Delivery`. |
| `400` | `source_not_found` | Unknown or disabled `source_id`. |
| `401` | `signature_invalid` | Missing signature header or HMAC mismatch. |
| `413` | `payload_too_large` | Body longer than 64 KiB. |
| `429` | `quota_exceeded` | User or global accepted-event quota. |
| `429` | `storage_admission_denied` | Retained-payload budget would be exceeded. |
| `503` | `worker_unhandled` | Validation passed but the durable write failed. Provider may retry. |

---

## 2. Control API (FastAPI on Render)

Base path: `/api`.

Header: `Authorization: Bearer <Supabase JWT>`.

Every query is tenant-scoped to the authenticated user. Cross-project ids return `404`, not `403`, so existence of another tenant’s rows is not leaked.

Control-plane error codes: `unauthorized`, `forbidden`, `not_found`, `validation_error`, `conflict`.

`401 unauthorized` — missing/invalid JWT.  
`404 not_found` — unknown id in this tenant.  
`409 conflict` — uniqueness (for example a duplicate scheduled suite run).  
`422 validation_error` — request body fails schema.

### Health

`GET /api/health` — unauthenticated.

`200` `{"status": "ok"}`

### Projects

`POST /api/projects`  
Body: `{"name": "string"}`  
`201` `{id, name, created_at}`

`GET /api/projects`  
`200` `{items: [{id, name, created_at}]}`

`GET /api/projects/{project_id}`  
`200` `{id, name, created_at}`

### Sources

Create a destination first, then a source. Ingest sends the Event to `destination_id`.

`POST /api/projects/{project_id}/sources`  
Body: `{"name": "string", "provider": "generic_hmac" | "github", "destination_id": "uuid"}`  
`201`:

```json
{
  "id": "uuid",
  "project_id": "uuid",
  "name": "billing-webhooks",
  "provider": "generic_hmac",
  "destination_id": "uuid",
  "ingest_url": "https://.../ingest/{source_id}",
  "secret": "plaintext-once",
  "created_at": "2026-10-09T00:00:00Z"
}
```

`secret` is returned **only on create** (and on rotate). It is never listed later.

`GET /api/projects/{project_id}/sources`  
`200` `{items: [{id, project_id, name, provider, destination_id, ingest_url, created_at}]}` — no secrets.

`GET /api/sources/{source_id}` — same public fields.

`POST /api/sources/{source_id}/rotate-secret`  
`200` `{secret}` — new plaintext once; old secret invalidated.

### Destinations

`POST /api/projects/{project_id}/destinations`

HTTPS:

```json
{"name": "staging", "type": "https", "url": "https://example.test/hooks"}
```

CLI:

```json
{"name": "local", "type": "cli"}
```

`201` `{id, project_id, name, type, url, signing_secret, created_at}`  
`signing_secret` is plaintext once, same rule as source `secret`. It is omitted from later GET and list. `url` is null for `cli`. HTTPS URLs are validated at write time against the SSRF rules; invalid is `422`.

`GET /api/projects/{project_id}/destinations`  
`GET /api/destinations/{destination_id}`

Each HTTPS destination has a **destination signing secret** used for outgoing HMAC. It is returned only on create/rotate, same pattern as source secrets:

`POST /api/destinations/{destination_id}/rotate-signing-secret` → `200` `{signing_secret}`

### Events

`GET /api/projects/{project_id}/events`

Query: `source_id`, `event_type`, `status` (`accepted` | `payload_purged`), `from`, `to` (RFC 3339), `limit` (default 50, max 100), `before` (cursor: event id).

No payload-body search (stretch).

`200`:

```json
{
  "items": [
    {
      "id": "uuid",
      "project_id": "uuid",
      "source_id": "uuid",
      "provider_delivery_id": "string",
      "event_type": "push",
      "status": "accepted",
      "byte_size": 1024,
      "correlation_id": "uuid",
      "accepted_at": "2026-10-09T00:00:00Z"
    }
  ],
  "next_before": "uuid or null"
}
```

`GET /api/events/{event_id}`  
Same fields plus `body` (raw string or null if purged), `content_type`, and `deliveries` (summary list: `id, destination_id, status, attempt_count, replayed_from_delivery_id, suite_run_id`).

### Deliveries and attempts

`GET /api/deliveries/{delivery_id}`

```json
{
  "id": "uuid",
  "event_id": "uuid",
  "destination_id": "uuid",
  "status": "queued",
  "replayed_from_delivery_id": null,
  "suite_run_id": null,
  "release_after": "2026-10-09T00:00:00Z",
  "attempts": [
    {
      "id": "uuid",
      "attempt_number": 1,
      "http_status": 500,
      "duration_ms": 812,
      "error_category": "destination_5xx",
      "response_excerpt": "...",
      "correlation_id": "uuid",
      "completed_at": "2026-10-09T00:00:01Z"
    }
  ]
}
```

`POST /api/deliveries/{delivery_id}/replay`  
`201` `{id, event_id, destination_id, status: "queued", replayed_from_delivery_id}`  
Creates a new Delivery + OutboxJob. Does not modify prior attempts.

### Suites

`POST /api/projects/{project_id}/suites`  
Body:

```json
{
  "name": "duplicate-and-delay",
  "source_id": "uuid",
  "destination_id": "uuid",
  "plan": {
    "kind": "duplicate" | "delay" | "reorder",
    "params": {}
  }
}
```

`plan.params` (locked shapes):

- `duplicate`: `{"count": 2}` (integer 2–5)
- `delay`: `{"delay_seconds": 30}` (1–3600)
- `reorder`: `{"event_ids": ["uuid", "uuid"], "release_offsets_seconds": [5, 0]}`  
  Same length arrays. Offsets are relative to suite-run start.

`GET /api/projects/{project_id}/suites`  
`GET /api/suites/{suite_id}`

`POST /api/suites/{suite_id}/runs`  
Body: `{"scheduled_for": "RFC3339 or null"}`  
`null` means run now; the server stores `scheduled_for` as the request timestamp.  
`201` `{id, suite_id, scheduled_for, status: "scheduled"}`  
`409 conflict` if `(suite_id, scheduled_for)` already exists.

`GET /api/suites/{suite_id}/runs`

### Relay tokens (CLI)

`POST /api/destinations/{destination_id}/relay-tokens`  
Destination `type` must be `cli`.  
`201`:

```json
{
  "token": "opaque-or-jwt",
  "expires_at": "2026-10-09T00:15:00Z",
  "websocket_url": "wss://.../api/relay"
}
```

TTL is in [timeouts-quotas.md](timeouts-quotas.md).

---

## 3. Relay WebSocket

`GET /api/relay` Upgrade, query `token`.

Invalid/expired token: close `4401`. Destination already connected: close `4409` (one live CLI per destination).

Heartbeat: server WebSocket ping every heartbeat interval; CLI answers pong. Silence beyond the disconnect threshold marks the session dead.

### Server → CLI

```json
{
  "type": "deliver",
  "attempt_id": "uuid",
  "delivery_id": "uuid",
  "headers": {"Content-Type": "application/json", "X-Webhook-Signature-256": "sha256=...", "X-Webhook-Timestamp": "1710000000", "X-Correlation-Id": "uuid"},
  "body": "<raw payload string>"
}
```

### CLI → server

```json
{
  "type": "result",
  "attempt_id": "uuid",
  "status": 200,
  "headers": {"Content-Type": "application/json"},
  "body": "<response excerpt, max 2048 bytes>",
  "duration_ms": 37
}
```

If localhost fails to connect: `status` null, omit body, the worker records `cli_disconnected` or the CLI sends `type: "error", "error_category": "cli_disconnected"`.

Duplicate `attempt_id`: CLI does not POST to localhost again; it may replay the stored result or ignore.

---

## 4. Outgoing HTTPS delivery (worker → destination)

Not a public API, but a locked wire contract so handlers and the staging receiver can verify.

- Method `POST`, no redirects (`allow_redirects=false`).
- Timeout: delivery HTTP timeout.
- Headers:
  - `Content-Type` — original event content type or `application/octet-stream`
  - `X-Webhook-Timestamp` — Unix seconds
  - `X-Webhook-Signature-256` — `sha256=` + hex HMAC-SHA256 of `{timestamp}.{raw_body}` using the **destination signing secret** (not the inbound source secret)
  - `X-Correlation-Id` — event correlation id
  - `X-Webhook-Event-Id` — platform Event id
  - `X-Webhook-Delivery-Id` — platform Delivery id
- Body: unmodified Event body.
- SSRF: reject non-HTTPS; resolve DNS; block private, loopback, link-local, and reserved addresses on the literal URL and on every resolved address. Re-check after resolve (DNS rebinding).

2xx is success. Anything else is a failed attempt with the matching error category.
