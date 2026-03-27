"""Tests for Q057 — Structured JSON logging with request IDs."""

import json
import logging
import os


class TestJsonFormatter:
    """Test that the JSON formatter produces valid JSON with expected fields."""

    def test_json_output_is_valid(self):
        """Log output must be valid JSON when using _JsonFormatter."""
        from api.dependencies import _JsonFormatter

        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="sp5.api",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "sp5.api"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_json_includes_request_id_from_context(self):
        """request_id from contextvars appears in JSON log output."""
        from api.dependencies import _JsonFormatter, request_id_ctx

        formatter = _JsonFormatter()
        token = request_id_ctx.set("test-uuid-1234")
        try:
            record = logging.LogRecord(
                name="sp5.api",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="With request ID",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["request_id"] == "test-uuid-1234"
        finally:
            request_id_ctx.reset(token)

    def test_json_includes_extra_fields(self):
        """Extra fields (method, path, status_code, duration_ms) appear in output."""
        from api.dependencies import _JsonFormatter

        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="sp5.api",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="request log",
            args=(),
            exc_info=None,
        )
        record.method = "GET"
        record.path = "/api/health"
        record.status_code = 200
        record.duration_ms = 42
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["method"] == "GET"
        assert parsed["path"] == "/api/health"
        assert parsed["status_code"] == 200
        assert parsed["duration_ms"] == 42

    def test_json_includes_exception(self):
        """Exception info is included when exc_info is set."""
        from api.dependencies import _JsonFormatter

        formatter = _JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="sp5.api",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="error occurred",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]


class TestTextFormatter:
    """Test the human-readable text formatter."""

    def test_text_output_not_json(self):
        from api.dependencies import _TextFormatter

        formatter = _TextFormatter()
        record = logging.LogRecord(
            name="sp5.api",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello text",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        # Should NOT be valid JSON
        with __import__("pytest").raises(json.JSONDecodeError):
            json.loads(output)
        assert "Hello text" in output


class TestRequestIdMiddleware:
    """Test that X-Request-ID is returned and incoming values are respected."""

    def test_response_has_request_id(self, sync_client):
        """Every response must include X-Request-ID header."""
        r = sync_client.get("/api/health")
        assert r.status_code == 200
        rid = r.headers.get("x-request-id")
        assert rid is not None
        assert len(rid) >= 8  # UUID format

    def test_incoming_request_id_is_echoed(self, sync_client):
        """If client sends X-Request-ID, it should be echoed back."""
        custom_id = "my-custom-trace-id-12345"
        r = sync_client.get("/api/health", headers={"X-Request-ID": custom_id})
        assert r.status_code == 200
        assert r.headers.get("x-request-id") == custom_id

    def test_request_id_is_uuid_format(self, sync_client):
        """Generated request IDs should be valid UUIDs."""
        import uuid

        r = sync_client.get("/api/version")
        rid = r.headers.get("x-request-id")
        # Should be parseable as UUID
        parsed = uuid.UUID(rid)
        assert str(parsed) == rid


class TestLogLevelConfig:
    """Test that SP5_LOG_LEVEL env var is respected."""

    def test_logger_level_from_env(self):
        """The sp5.api logger should respect SP5_LOG_LEVEL."""
        from api.dependencies import _logger

        # Default is INFO or whatever SP5_LOG_LEVEL is set to
        level_name = os.environ.get("SP5_LOG_LEVEL", "INFO").upper()
        expected = getattr(logging, level_name, logging.INFO)
        assert _logger.level == expected

    def test_logger_name(self):
        """Logger should be named sp5.api."""
        from api.dependencies import _logger

        assert _logger.name == "sp5.api"
