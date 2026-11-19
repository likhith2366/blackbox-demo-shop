# payment-svc runbook

Service: `payment-svc`, port 18102, endpoint `POST /charge`. Backed by SQLite
(`config/payment-svc.yaml`: `db.pool_size`, `db.acquire_timeout_ms`).

## Health
`GET /health` on port 18102.

## Useful signals
- metrics: `error_rate`, `latency_p95_ms`, `db_pool_in_use`, `db_pool_wait_p95_ms`, `db_query_p95_ms`
- logs: `charge failed` lines carry the error and the order id

## First checks
1. Is the pool saturated (`db_pool_in_use` at `pool_size`)? Check pool wait times.
2. Did a deploy or config change land shortly before the alert?
3. Are callers sending more traffic than usual (requests_per_s)?

## Duplicate charges
`/charge` is idempotent on `order_id` (DEMO-18). Repeated calls for the same order should
return the existing payment, not a second row.
