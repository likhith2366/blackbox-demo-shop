"""Default call policy for internal service-to-service HTTP calls.

Services use these values unless their own config overrides a field.
"""

DEFAULT_TIMEOUT_MS = 250
MAX_RETRIES = 4
# Which failures are retried: "connect_error", "timeout", "5xx"
RETRY_ON = ("connect_error", "timeout", "5xx")
BACKOFF_MS = 0
