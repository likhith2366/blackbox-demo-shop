# Configuration

Each service reads `config/<service>.yaml` at startup.

## checkout-svc
| key | meaning |
|---|---|
| `port` | listen port (18101) |
| `log_level` | log level |
| `payment.base_url` | payment-svc address |

The payment call's timeout and retry policy default to `libs/httpclient/policy.py`
(`DEFAULT_TIMEOUT_MS`, `MAX_RETRIES`, `RETRY_ON`, `BACKOFF_MS`) unless set under `payment:`.

## payment-svc
| key | meaning |
|---|---|
| `port` | listen port (18102) |
| `db.path` | SQLite file, overridden by `PAYMENT_DB_PATH` when deployed |
| `db.pool_size` | connection pool size |
| `db.acquire_timeout_ms` | how long a request waits for a pooled connection |
