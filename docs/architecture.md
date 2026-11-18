# Architecture

```
client -> checkout-svc (POST /orders) -> payment-svc (POST /charge) -> SQLite
```

- checkout-svc calls payment-svc through `libs/httpclient`, which applies the timeout and
  retry policy and propagates the trace header.
- Both services write JSON logs, spans and metric points through `libs/telemetry`.
- Releases are immutable artifacts; `BUILD_INFO.json` records build id, commit and digest.
