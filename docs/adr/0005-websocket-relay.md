# ADR 0005 — WebSocket relay

## Status

Accepted

## Context

Local CLI destinations cannot receive a public HTTPS POST. The control plane already runs as one Render free FastAPI service, which can idle or restart. The CLI therefore needs an authenticated, reconnectable channel that forwards a delivery attempt to localhost and returns the handler’s response to the worker.

Putting the relay on the same service as the control API keeps the free-tier footprint at one web process. The tradeoff is leftover connections when Render sleeps, which must be visible and recoverable rather than silent.

## Decision

- The CLI opens a WebSocket to the FastAPI relay using a **short-lived relay token** minted by `POST /v1/destinations/{id}/relay-tokens` (JWT-authenticated, tenant-scoped). The token is sent on connect, not as a long-lived API key.
- Protocol: server pushes `{attempt_id, delivery_id, headers, body}`; CLI POSTs to localhost, then replies `{attempt_id, status, headers, body, duration_ms}`. Heartbeat is WebSocket ping/pong.
- A dropped socket is an ordinary failed attempt (`cli_disconnected` or `cli_timeout`). SQS retries like any other destination failure.
- The CLI reconnects with exponential backoff. Duplicate suppression key is **`attempt_id`**: if the same attempt is forwarded twice (reconnect racing a redelivery), localhost is POSTed at most once for that id.
- Render idle/restart is expected. The project does not claim an uptime SLA; the CLI and dashboard must show disconnects.

The worker talks to the relay over an internal HTTP request (or the relay looks up the connected socket by destination id). The destination URL for CLI destinations is not a public URL; it is a `cli` destination type bound to the user’s relay session.

## Consequences

- Local delivery depends on the CLI being connected. If it is not, attempts fail and count toward the five receives.
- Relay tokens expire; the CLI must refresh via the control API.
- Duplicate suppression is CLI-side (by `attempt_id`) in addition to worker-side “already succeeded” guards.
- Scaling the relay to many concurrent CLIs is out of MVP scope; one process and a connection map is enough for the demo user limit.
