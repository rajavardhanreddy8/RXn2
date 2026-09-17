import io
import urllib.error

from scripts.run_epo_acquisition_agent import retryable


def http_error(code: int):
    return urllib.error.HTTPError("https://example.test", code, "error", {}, io.BytesIO())


def test_only_transient_http_errors_are_retried():
    assert retryable(http_error(429)) is True
    assert retryable(http_error(503)) is True
    assert retryable(http_error(404)) is False
    assert retryable(ValueError("bad publication")) is False
