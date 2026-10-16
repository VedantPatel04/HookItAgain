# Database constraints

This is the schema spec for Alembic in Sprint 1 section 2. It is not a migration.

Engine: Supabase PostgreSQL. All primary keys are `UUID` (`gen_random_uuid()`). All tenant tables include `project_id` and are queried with it.

Statuses are `TEXT` with `CHECK` constraints using the enums in [state-machines.md](state-machines.md). Do not use Postgres enums (they make migrations brittle); CHECKs match the docs.

Alembic create order: `profiles` → `projects` → `destinations` → `sources` → `source_secrets` → `events` → `scenario_suites` → `suite_runs` → `deliveries` → `outbox_jobs` → `delivery_attempts` → `usage_monthly` / `usage_global_monthly`.

## Tenant rule

Every foreign key that points at a project-owned row must not be able to reference a row in another project. Practical pattern: child tables copy `project_id`, and composite foreign keys such as `(project_id, source_id)` reference `(project_id, id)` on the parent. Every project-owned table therefore has `UNIQUE (project_id, id)` in addition to the primary key, so those composites are legal. Application queries still filter `project_id` from the JWT/user mapping; the composites are the last line of defense.

Users map to projects through an ownership table so ingest (which has no JWT) can still attribute quota to a user.

## Tables

### `profiles`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `user_id` | UUID PK | Matches Supabase Auth `auth.users.id`. |
| `github_login` | TEXT | |
| `created_at` | TIMESTAMPTZ NOT NULL | |

### `projects`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `user_id` | UUID NOT NULL FK → profiles | Owner. MVP is single-user projects (no team roles). |
| `name` | TEXT NOT NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL | |

### `destinations`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL FK → projects | |
| `name` | TEXT NOT NULL | |
| `type` | TEXT NOT NULL | CHECK `https` \| `cli`. |
| `url` | TEXT | Required for `https`, null for `cli`. CHECK: type/url consistent. |
| `signing_secret_ciphertext` | BYTEA NOT NULL | Outgoing HMAC secret. Fernet ciphertext. One process key. Plaintext is never stored. |
| `created_at` | TIMESTAMPTZ NOT NULL | |

CHECK: `type = 'https'` implies `url IS NOT NULL`; `type = 'cli'` implies `url IS NULL`. Unique `(project_id, id)` for composite FKs.

Create destinations before sources. A project needs at least one destination before a source can point at it.

### `sources`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | Path segment of the ingest URL. |
| `project_id` | UUID NOT NULL FK → projects | |
| `name` | TEXT NOT NULL | |
| `provider` | TEXT NOT NULL | CHECK `generic_hmac` \| `github`. |
| `destination_id` | UUID NOT NULL | Default destination for ingest. Composite FK `(project_id, destination_id)` → `destinations`. |
| `disabled_at` | TIMESTAMPTZ | Null means active. |
| `created_at` | TIMESTAMPTZ NOT NULL | |

Unique `(project_id, id)` for composite FKs from children.

### `source_secrets`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `source_id` | UUID PK FK → sources | One active secret per source. |
| `project_id` | UUID NOT NULL | Composite FK with `source_id`. |
| `ciphertext` | BYTEA NOT NULL | Fernet ciphertext. Same process key as destination signing secrets. Never selected by list APIs. Plaintext is never stored. |
| `created_at` | TIMESTAMPTZ NOT NULL | |

### `events`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL | |
| `source_id` | UUID NOT NULL | Composite FK `(project_id, source_id)`. |
| `provider_delivery_id` | TEXT NOT NULL | |
| `event_type` | TEXT | From `X-GitHub-Event` / `X-Webhook-Event`. |
| `content_type` | TEXT | |
| `body` | BYTEA | Null after purge. |
| `byte_size` | INTEGER NOT NULL | Original size; kept after purge until we drop the row. CHECK `>= 0` and `<= 65536`. |
| `status` | TEXT NOT NULL | CHECK `accepted` \| `payload_purged`. |
| `correlation_id` | UUID NOT NULL | |
| `accepted_at` | TIMESTAMPTZ NOT NULL | |
| `purged_at` | TIMESTAMPTZ | |

**UNIQUE (`source_id`, `provider_delivery_id`)** — one Event per provider retry identity.

CHECK: `status = 'payload_purged'` implies `body IS NULL` and `purged_at IS NOT NULL`; `status = 'accepted'` implies `body IS NOT NULL`.

Append-only except `body`/`status`/`purged_at` on retention. Never update `provider_delivery_id`, `byte_size`, or `correlation_id`.

### `scenario_suites`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL | |
| `source_id` | UUID NOT NULL | Composite FK. |
| `destination_id` | UUID NOT NULL | Composite FK. |
| `name` | TEXT NOT NULL | |
| `plan_kind` | TEXT NOT NULL | CHECK `duplicate` \| `delay` \| `reorder`. |
| `plan_params` | JSONB NOT NULL | Shapes in [api-contracts.md](api-contracts.md). |
| `created_at` | TIMESTAMPTZ NOT NULL | |

### `suite_runs`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL | |
| `suite_id` | UUID NOT NULL | Composite FK. |
| `scheduled_for` | TIMESTAMPTZ NOT NULL | |
| `status` | TEXT NOT NULL | CHECK `scheduled` \| `running` \| `completed` \| `failed`. |
| `error_message` | TEXT | Set on `failed`. |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `started_at` | TIMESTAMPTZ | |
| `finished_at` | TIMESTAMPTZ | |

**UNIQUE (`suite_id`, `scheduled_for`)** — one run per suite per planned time. Manual runs set `scheduled_for` to the request timestamp.

Claim: `UPDATE suite_runs SET status = 'running', started_at = now() WHERE id = $1 AND status = 'scheduled'`.

### `deliveries`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | |
| `project_id` | UUID NOT NULL | |
| `event_id` | UUID NOT NULL | Composite FK `(project_id, event_id)`. |
| `destination_id` | UUID NOT NULL | Composite FK `(project_id, destination_id)`. |
| `status` | TEXT NOT NULL | CHECK `queued` \| `in_flight` \| `succeeded` \| `dead_lettered`. |
| `replayed_from_delivery_id` | UUID FK → deliveries | Null for the initial delivery. Same `project_id`. |
| `suite_run_id` | UUID FK → suite_runs | Null if not from a suite. |
| `release_after` | TIMESTAMPTZ NOT NULL | Eligible for SQS publish when `<= now()`. Initial ingest uses `accepted_at`. |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

Initial ingest creates one Delivery to `sources.destination_id`. Replay and suite expansion may target the same or another destination in the project. Look up the outbox row by `outbox_jobs.delivery_id`; do not put a reverse FK on Delivery (that would cycle).

### `outbox_jobs`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | Value published to SQS. |
| `project_id` | UUID NOT NULL | |
| `delivery_id` | UUID NOT NULL | UNIQUE. One outbox row per Delivery. Composite FK. |
| `status` | TEXT NOT NULL | CHECK `pending` \| `enqueued`. |
| `release_after` | TIMESTAMPTZ NOT NULL | Copied from the Delivery. |
| `created_at` | TIMESTAMPTZ NOT NULL | |
| `enqueued_at` | TIMESTAMPTZ | Set when `enqueued`. |
| `last_error` | TEXT | Last `SendMessage` error, if any. |

Reconciler predicate: `status = 'pending' AND release_after <= now() AND created_at <= now() - enqueue_grace`.

### `delivery_attempts`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `id` | UUID PK | This is the relay `attempt_id`. |
| `project_id` | UUID NOT NULL | |
| `delivery_id` | UUID NOT NULL | Composite FK. |
| `attempt_number` | INTEGER NOT NULL | Equals SQS `ApproximateReceiveCount`. CHECK `>= 1` AND `<= 5`. |
| `http_status` | INTEGER | Null if no HTTP response. |
| `duration_ms` | INTEGER NOT NULL | CHECK `>= 0`. |
| `error_category` | TEXT | Null on 2xx. Else a value from [error-categories.md](error-categories.md) attempt set. |
| `response_excerpt` | TEXT | Max 2048 chars. |
| `correlation_id` | UUID NOT NULL | Copied from the Event. |
| `completed_at` | TIMESTAMPTZ NOT NULL | |

**UNIQUE (`delivery_id`, `attempt_number`)**.

No `UPDATE` of attempt outcomes. Insert-only.

2xx attempts: `error_category IS NULL` and `http_status BETWEEN 200 AND 299`.  
Failed attempts: `error_category IS NOT NULL`.

### `usage_monthly`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `user_id` | UUID NOT NULL FK → profiles | |
| `year_month` | DATE NOT NULL | First day of the UTC month. |
| `accepted_count` | INTEGER NOT NULL | CHECK `>= 0`. |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

PRIMARY KEY `(user_id, year_month)`. Ingest increments this row in the same transaction as the Event insert.

### `usage_global_monthly`

| Column | Type | Notes |
| ------ | ---- | ----- |
| `year_month` | DATE PK | First day of the UTC month. |
| `accepted_count` | INTEGER NOT NULL | CHECK `>= 0`. |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

Tracks the 10 000 global cap. Incremented in the same ingest transaction as `usage_monthly`.

## Uniqueness (why)

- `(source_id, provider_delivery_id)` — the provider retry identity. Two POSTs with the same pair cannot become two Events.
- `(delivery_id, attempt_number)` — one stored row per SQS receive of that delivery. A worker glitch cannot record attempt 3 twice.
- `(suite_id, scheduled_for)` — a double EventBridge fire cannot create two expansions.
- `outbox_jobs.delivery_id` UNIQUE — a Delivery is published through exactly one outbox row.

## Indexes (list/filter and reconciler)

- `events (project_id, accepted_at DESC)`
- `events (project_id, source_id, accepted_at DESC)`
- `events (project_id, event_type, accepted_at DESC)`
- `events (project_id, status, accepted_at DESC)`
- `deliveries (event_id)`
- `deliveries (status)` where status is `queued` or `in_flight` (partial, optional)
- `outbox_jobs (status, release_after, created_at)` where `status = 'pending'`
- `delivery_attempts (delivery_id, attempt_number)`
- `suite_runs (suite_id, scheduled_for)`

## Retention and budget

A scheduled job (can share the reconciler cadence) finds `events.status = 'accepted' AND accepted_at < now() - 7 days`, sets `body = NULL`, `status = 'payload_purged'`, `purged_at = now()`.

Retained-payload budget is `SUM(byte_size)` for `status = 'accepted'` only. Purged events no longer count. Ingest admits a new body only if `current_sum + new_byte_size <= retained_payload_budget_bytes`.

## Append-only policy

- Do not `UPDATE` `delivery_attempts` at all.
- Do not `UPDATE` `events` except the retention purge columns (`body`, `status`, `purged_at`).
- Replay inserts a new `deliveries` row. It does not reset `status` on the old row.

## Quota counters

Increment `usage_monthly` and `usage_global_monthly` in the same ingest transaction as the Event. Serializing on those rows avoids counting with a table scan under concurrent ingest. If either increment would exceed [timeouts-quotas.md](timeouts-quotas.md), roll back and return `429`.
