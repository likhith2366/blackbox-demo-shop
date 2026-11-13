from libs.httpclient import policy


def test_timeout_is_positive():
    assert policy.DEFAULT_TIMEOUT_MS > 0


def test_retry_settings_are_sane():
    assert policy.MAX_RETRIES >= 0
    assert policy.BACKOFF_MS >= 0
    assert set(policy.RETRY_ON) <= {"connect_error", "timeout", "5xx"}
