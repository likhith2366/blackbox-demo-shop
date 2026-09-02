"""Default call policy for internal service-to-service HTTP calls.

Services use these values unless their own config overrides a field.
"""

DEFAULT_TIMEOUT_MS = 2000
MAX_RETRIES = 0
# Which failures are retried: "connect_error", "timeout", "5xx"
RETRY_ON = ()
BACKOFF_MS = 100
