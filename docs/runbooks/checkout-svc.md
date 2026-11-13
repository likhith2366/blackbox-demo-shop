# checkout-svc runbook

Service: `checkout-svc`, port 18101, endpoint `POST /orders`. Charges each order through
payment-svc (`payment.base_url` in `config/checkout-svc.yaml`) using the shared client in
`libs/httpclient`.

## Health
`GET /health` on port 18101.

## Useful signals
- metrics: `error_rate`, `latency_p95_ms`, `requests_per_s`, `upstream_attempts_per_s`, `upstream_errors_per_s`
- logs: payment call failures include the order id and attempt count

## First checks
1. Is the error coming from checkout itself or from the payment call?
2. Compare upstream attempts with incoming requests.
3. Check recent deploys of checkout-svc and changes to `libs/httpclient`.
