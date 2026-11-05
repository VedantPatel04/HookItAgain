# Baseline log

Record **baseline → change → measured result** here. Use the protocol in [metrics.md](metrics.md). Do not invent values.

Environment for the initial vertical slice (2026-11-03, git SHA `37a6c7c`): stack `dev`, region `us-east-1`. Ingest and worker memory 256 MB. Ingest reserved concurrency 5, worker reserved concurrency 2. SQLAlchemy `pool_size` 1. Receiver `https://hookitagain.onrender.com/api/staging/receive`. Live ingest Lambda timeout was 10 seconds.

| Date | Git SHA | Environment | Metric | Baseline | Change | Measured result | Conditions |
| ---- | ------- | ----------- | ------ | -------- | ------ | --------------- | ---------- |
| 2026-11-03 | 37a6c7c | `dev` us-east-1, see environment block | `ingestion_ack_latency_ms` p50/p95 | (none yet) | initial vertical slice | p50 765 ms, p95 992 ms | 1024-byte JSON, N=100 sequential, controlled receiver. Measured as client POST start to `202`. |
| 2026-11-03 | 37a6c7c | `dev` us-east-1, see environment block | `delivery_latency_ms` p50/p95 | (none yet) | initial vertical slice | p50 766 ms, p95 30829 ms | same. Measured as Event `accepted_at` to first 2xx `completed_at`. |
| 2026-11-03 | 37a6c7c | `dev` us-east-1, see environment block | `accepted_events_total` |  | initial vertical slice | 100 | expect 100 |
| 2026-11-03 | 37a6c7c | `dev` us-east-1, see environment block | `unaccounted_events_total` |  | initial vertical slice | 0 | expect 0. All 100 deliveries `succeeded`. |

Add rows beneath this table for later PRs. Keep conditions identical to the baseline you are comparing, or say what differed.
