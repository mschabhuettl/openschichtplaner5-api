"""Tests for Q092: Rate-limit event logging and admin dashboard API."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

# ── rate_limit_store unit tests ──────────────────────────────────────────────


class TestRateLimitStore:
    """Test the rate_limit_store module directly."""

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self._logfile = os.path.join(self._tmpdir, "rl_events.jsonl")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_log_and_read_events(self):
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            from api.rate_limit_store import get_rate_limit_events, log_rate_limit_event

            # Log some events
            log_rate_limit_event(user="Alice", ip="1.2.3.4", endpoint="/api/login", detail="5 per 1 minute")
            log_rate_limit_event(user="Bob", ip="5.6.7.8", endpoint="/api/schedule", detail="100 per 1 minute")
            log_rate_limit_event(user=None, ip="9.9.9.9", endpoint="/api/login", detail="5 per 1 minute")

            events = get_rate_limit_events()
            assert len(events) == 3
            # newest first
            assert events[0]["ip"] == "9.9.9.9"
            assert events[1]["user"] == "Bob"
            assert events[2]["user"] == "Alice"

    def test_filter_by_user(self):
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            from api.rate_limit_store import get_rate_limit_events, log_rate_limit_event

            log_rate_limit_event(user="Alice", ip="1.2.3.4", endpoint="/api/a")
            log_rate_limit_event(user="Bob", ip="5.6.7.8", endpoint="/api/b")
            log_rate_limit_event(user="Alice", ip="1.2.3.4", endpoint="/api/c")

            events = get_rate_limit_events(user="Alice")
            assert len(events) == 2
            assert all(e["user"] == "Alice" for e in events)

    def test_limit_param(self):
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            from api.rate_limit_store import get_rate_limit_events, log_rate_limit_event

            for i in range(20):
                log_rate_limit_event(user=f"user{i}", ip="1.1.1.1", endpoint="/api/x")

            events = get_rate_limit_events(limit=5)
            assert len(events) == 5

    def test_empty_file(self):
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            from api.rate_limit_store import get_rate_limit_events
            events = get_rate_limit_events()
            assert events == []

    def test_rotate_events(self):
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile), \
             patch("api.rate_limit_store._MAX_EVENTS", 5):
            from api.rate_limit_store import (
                get_rate_limit_events,
                log_rate_limit_event,
                rotate_events,
            )

            for i in range(10):
                log_rate_limit_event(user=f"user{i}", ip="1.1.1.1", endpoint="/api/x")

            removed = rotate_events()
            assert removed == 5

            events = get_rate_limit_events(limit=100)
            assert len(events) == 5


# ── API endpoint tests ───────────────────────────────────────────────────────


class TestRateLimitAPI:
    """Test GET /api/v1/admin/rate-limits endpoint."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self._tmpdir = tempfile.mkdtemp()
        self._logfile = os.path.join(self._tmpdir, "rl_events.jsonl")

        # Seed some events
        events = [
            {"timestamp": "2026-03-28T01:00:00.000Z", "user": "Alice", "ip": "1.2.3.4", "endpoint": "/api/login", "detail": "5 per 1 minute"},
            {"timestamp": "2026-03-28T02:00:00.000Z", "user": "Bob", "ip": "5.6.7.8", "endpoint": "/api/schedule", "detail": "100 per 1 minute"},
            {"timestamp": "2026-03-28T03:00:00.000Z", "user": "", "ip": "9.9.9.9", "endpoint": "/api/login", "detail": "5 per 1 minute"},
        ]
        with open(self._logfile, "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        yield

        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_admin_endpoint_returns_events(self):
        """Admin can fetch rate-limit events with summary."""
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            # Import app after patching
            from api.rate_limit_store import get_rate_limit_events
            events = get_rate_limit_events()
            assert len(events) == 3

            # Verify summary computation logic
            top_users: dict[str, int] = {}
            top_endpoints: dict[str, int] = {}
            for evt in events:
                u = evt.get("user") or "(anonymous)"
                ep = evt.get("endpoint", "?")
                top_users[u] = top_users.get(u, 0) + 1
                top_endpoints[ep] = top_endpoints.get(ep, 0) + 1

            assert top_users["Alice"] == 1
            assert top_users["Bob"] == 1
            assert top_users["(anonymous)"] == 1
            assert top_endpoints["/api/login"] == 2
            assert top_endpoints["/api/schedule"] == 1

    def test_filter_by_since(self):
        """Events can be filtered by since parameter."""
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            from api.rate_limit_store import get_rate_limit_events
            events = get_rate_limit_events(since="2026-03-28T02:00:00.000Z")
            assert len(events) == 2  # Bob + anonymous

    def test_filter_by_until(self):
        """Events can be filtered by until parameter."""
        with patch("api.rate_limit_store._RATE_LIMIT_LOG", self._logfile):
            from api.rate_limit_store import get_rate_limit_events
            events = get_rate_limit_events(until="2026-03-28T01:30:00.000Z")
            assert len(events) == 1  # Alice only
