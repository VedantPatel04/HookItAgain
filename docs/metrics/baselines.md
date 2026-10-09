# Baseline log

Record **baseline → change → measured result** here. Use the protocol in [metrics.md](metrics.md). Leave rows empty until Sprint 1 section 3 produces the first vertical-slice numbers. Do not invent values.

| Date | Git SHA | Environment | Metric | Baseline | Change | Measured result | Conditions |
| ---- | ------- | ----------- | ------ | -------- | ------ | --------------- | ---------- |
|  |  |  | `ingestion_ack_latency_ms` p50/p95 | (none yet) | initial vertical slice |  | 1024-byte payload, N=100 sequential, controlled receiver |
|  |  |  | `delivery_latency_ms` p50/p95 | (none yet) | initial vertical slice |  | same |
|  |  |  | `accepted_events_total` |  | initial vertical slice |  | expect 100 |
|  |  |  | `unaccounted_events_total` |  | initial vertical slice |  | expect 0 |

Add rows beneath this table for later PRs. Keep conditions identical to the baseline you are comparing, or say what differed.
