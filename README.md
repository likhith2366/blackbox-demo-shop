# shop-platform

Services for the demo shop.

| Path | What |
|---|---|
| `services/checkout` | checkout-svc: `POST /orders`, charges via payment-svc |
| `services/payment` | payment-svc: `POST /charge`, SQLite-backed payments store |
| `libs/httpclient` | shared internal HTTP client (timeouts, retries, trace propagation) |
| `libs/telemetry` | JSON logs, spans and metric points |
| `libs/web` | small HTTP service scaffold |
| `config/` | per-service runtime configuration |
| `db/migrations` | payment database migrations, applied by payment-svc at startup |

Releases are built by the build pipeline into immutable artifacts; see BUILD_INFO.json
inside an artifact for its build id, commit and digest.

## Running locally

Start payment-svc first, then checkout-svc, from the repo root:

```
python -m services.payment.app
python -m services.checkout.app
```

Ports come from `config/`: checkout-svc 18101, payment-svc 18102. Both expose `GET /health`.
