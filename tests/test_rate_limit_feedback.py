"""Tests for Q055 — Rate-limit 429 response format and Retry-After header."""

import json
import os
import sys
from unittest.mock import MagicMock  # noqa: I001

# Python path setup
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("SP5_DEV_MODE", "true")

from api.main import _rate_limit_exceeded_handler, app  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402


def _make_exc(detail_str: str) -> RateLimitExceeded:
    """Create a RateLimitExceeded with a given detail string (bypassing Limit object)."""
    exc = RateLimitExceeded.__new__(RateLimitExceeded)
    exc.status_code = 429
    exc.detail = detail_str
    exc.limit = None
    return exc


def _make_request(path: str = "/api/test") -> MagicMock:
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.url.path = path
    request.app = app
    request.state.view_rate_limit = None
    return request


class TestRateLimitHandler:
    """Test the custom 429 error handler."""

    def test_handler_returns_structured_json(self):
        """Verify the handler returns the expected JSON structure."""
        request = _make_request()
        exc = _make_exc("Rate limit exceeded: 5 per 1 minute")
        response = _rate_limit_exceeded_handler(request, exc)

        assert response.status_code == 429
        body = json.loads(response.body.decode())
        assert body["error"] == "rate_limited"
        assert "retry_after" in body
        assert isinstance(body["retry_after"], int)
        assert body["retry_after"] > 0
        assert "message" in body
        assert "Sekunden" in body["message"]
        assert "detail" in body

    def test_handler_includes_retry_after_header(self):
        """Verify Retry-After header is present in 429 responses."""
        request = _make_request()
        exc = _make_exc("Rate limit exceeded: 5 per 1 minute")
        response = _rate_limit_exceeded_handler(request, exc)

        assert "retry-after" in response.headers
        retry_val = int(response.headers["retry-after"])
        assert retry_val > 0

    def test_handler_parses_minute_limit(self):
        """Verify retry_after is correctly parsed for per-minute limits."""
        request = _make_request()
        exc = _make_exc("Rate limit exceeded: 5 per 1 minute")
        response = _rate_limit_exceeded_handler(request, exc)

        body = json.loads(response.body.decode())
        assert body["retry_after"] == 60

    def test_handler_parses_hour_limit(self):
        """Verify retry_after is correctly parsed for per-hour limits."""
        request = _make_request()
        exc = _make_exc("Rate limit exceeded: 10 per 1 hour")
        response = _rate_limit_exceeded_handler(request, exc)

        body = json.loads(response.body.decode())
        assert body["retry_after"] == 3600

    def test_handler_default_retry_when_unparseable(self):
        """Verify fallback retry_after when detail is not parseable."""
        request = _make_request()
        exc = _make_exc("Some unexpected format")
        response = _rate_limit_exceeded_handler(request, exc)

        body = json.loads(response.body.decode())
        assert body["retry_after"] == 60  # default fallback
        assert body["error"] == "rate_limited"

    def test_handler_parses_second_limit(self):
        """Verify retry_after for per-second limits."""
        request = _make_request()
        exc = _make_exc("Rate limit exceeded: 1 per 5 second")
        response = _rate_limit_exceeded_handler(request, exc)

        body = json.loads(response.body.decode())
        assert body["retry_after"] == 5

    def test_message_contains_seconds_count(self):
        """Verify the message includes the actual seconds count."""
        request = _make_request()
        exc = _make_exc("Rate limit exceeded: 5 per 1 minute")
        response = _rate_limit_exceeded_handler(request, exc)

        body = json.loads(response.body.decode())
        assert "60 Sekunden" in body["message"]
