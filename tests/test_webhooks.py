"""Tests for webhook CRUD + delivery logic."""

import hashlib
import hmac
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.dependencies import require_admin  # noqa: E402
from api.routers.webhooks import (  # noqa: E402
    VALID_EVENTS,
    _load_webhooks,
    _save_webhooks,
    deliver_webhook,
    dispatch_event,
    sign_payload,
)
from api.routers.webhooks import router as webhooks_router  # noqa: E402

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_webhooks_file(tmp_path, monkeypatch):
    """Use a temp file for webhooks storage during tests."""
    test_file = str(tmp_path / "webhooks.json")
    monkeypatch.setattr("api.routers.webhooks._WEBHOOKS_FILE", test_file)
    return test_file


@pytest.fixture
def sample_webhook():
    return {
        "id": 1,
        "url": "https://example.com/hook",
        "name": "Test Webhook",
        "events": ["shift.created", "shift.updated"],
        "secret": "abc123secret",
        "active": True,
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_delivery": None,
    }


# ── HMAC Signing Tests ──────────────────────────────────────────


class TestSignPayload:
    def test_sign_payload_returns_hex(self):
        sig = sign_payload("mysecret", b'{"event": "test"}')
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest

    def test_sign_payload_is_deterministic(self):
        payload = b'{"data": "hello"}'
        sig1 = sign_payload("key", payload)
        sig2 = sign_payload("key", payload)
        assert sig1 == sig2

    def test_sign_payload_differs_with_different_key(self):
        payload = b'{"data": "hello"}'
        sig1 = sign_payload("key1", payload)
        sig2 = sign_payload("key2", payload)
        assert sig1 != sig2

    def test_sign_matches_manual_hmac(self):
        secret = "test_secret"
        payload = b'{"event": "shift.created"}'
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert sign_payload(secret, payload) == expected


# ── Storage Tests ────────────────────────────────────────────────


class TestStorage:
    def test_load_empty(self):
        webhooks = _load_webhooks()
        assert webhooks == []

    def test_save_and_load(self, sample_webhook):
        _save_webhooks([sample_webhook])
        loaded = _load_webhooks()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Test Webhook"
        assert loaded[0]["url"] == "https://example.com/hook"

    def test_save_multiple(self, sample_webhook):
        wh2 = {**sample_webhook, "id": 2, "name": "Second"}
        _save_webhooks([sample_webhook, wh2])
        loaded = _load_webhooks()
        assert len(loaded) == 2


# ── Delivery Tests ───────────────────────────────────────────────


class TestDelivery:
    @pytest.mark.asyncio
    async def test_deliver_success(self, sample_webhook):
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routers.webhooks.httpx.AsyncClient", return_value=mock_client):
            result = await deliver_webhook(sample_webhook, "shift.created", {"id": 1})

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["attempt"] == 1

    @pytest.mark.asyncio
    async def test_deliver_retry_on_500(self, sample_webhook):
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500

        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[mock_response_fail, mock_response_ok])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routers.webhooks.httpx.AsyncClient", return_value=mock_client):
            with patch("api.routers.webhooks.BACKOFF_MS", 1):  # Fast backoff for tests
                result = await deliver_webhook(
                    sample_webhook, "shift.created", {"id": 1}
                )

        assert result["success"] is True
        assert result["attempt"] == 2

    @pytest.mark.asyncio
    async def test_deliver_all_retries_fail(self, sample_webhook):
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routers.webhooks.httpx.AsyncClient", return_value=mock_client):
            with patch("api.routers.webhooks.BACKOFF_MS", 1):
                result = await deliver_webhook(
                    sample_webhook, "shift.created", {"id": 1}
                )

        assert result["success"] is False
        assert result["attempt"] == 3
        assert "HTTP 500" in result["error"]

    @pytest.mark.asyncio
    async def test_deliver_network_error_retries(self, sample_webhook):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routers.webhooks.httpx.AsyncClient", return_value=mock_client):
            with patch("api.routers.webhooks.BACKOFF_MS", 1):
                result = await deliver_webhook(
                    sample_webhook, "shift.created", {"id": 1}
                )

        assert result["success"] is False
        assert "Connection refused" in result["error"]

    @pytest.mark.asyncio
    async def test_deliver_sends_hmac_header(self, sample_webhook):
        mock_response = MagicMock()
        mock_response.status_code = 200

        captured_kwargs = {}

        async def capture_post(url, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routers.webhooks.httpx.AsyncClient", return_value=mock_client):
            await deliver_webhook(sample_webhook, "shift.created", {"id": 1})

        headers = captured_kwargs.get("headers", {})
        assert "X-SP5-Signature" in headers
        assert "X-SP5-Event" in headers
        assert headers["X-SP5-Event"] == "shift.created"

        # Verify HMAC matches
        payload = captured_kwargs["content"]
        expected_sig = sign_payload(sample_webhook["secret"], payload)
        assert headers["X-SP5-Signature"] == expected_sig


# ── Dispatch Tests ───────────────────────────────────────────────


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_filters_by_event(self, sample_webhook):
        _save_webhooks([sample_webhook])

        with patch("api.routers.webhooks.deliver_webhook", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = {
                "success": True,
                "status_code": 200,
                "attempt": 1,
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
            # shift.created should match
            await dispatch_event("shift.created", {"id": 1})
            mock_deliver.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_skips_non_matching_event(self, sample_webhook):
        _save_webhooks([sample_webhook])

        with patch("api.routers.webhooks.deliver_webhook", new_callable=AsyncMock) as mock_deliver:
            # absence.created is not in sample_webhook events
            await dispatch_event("absence.created", {"id": 1})
            mock_deliver.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_skips_inactive_webhook(self, sample_webhook):
        sample_webhook["active"] = False
        _save_webhooks([sample_webhook])

        with patch("api.routers.webhooks.deliver_webhook", new_callable=AsyncMock) as mock_deliver:
            await dispatch_event("shift.created", {"id": 1})
            mock_deliver.assert_not_called()


# ── CRUD Tests (via FastAPI TestClient) ──────────────────────────


def _mock_admin():
    return {"ID": 1, "NAME": "TestAdmin", "role": "Admin"}


_test_app = FastAPI()
_test_app.include_router(webhooks_router)
_test_app.dependency_overrides[require_admin] = _mock_admin


@pytest.fixture
def client():
    return TestClient(_test_app)


class TestCRUDEndpoints:
    def test_list_empty(self, client):
        resp = client.get("/api/webhooks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_webhook(self, client):
        resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "My Webhook",
                "events": ["shift.created"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["record"]["name"] == "My Webhook"
        assert data["record"]["url"] == "https://example.com/hook"
        assert data["record"]["events"] == ["shift.created"]
        assert data["record"]["active"] is True
        assert len(data["record"]["secret"]) == 64  # hex token

    def test_list_after_create(self, client):
        client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "Test",
                "events": ["shift.created"],
            },
        )
        resp = client.get("/api/webhooks")
        assert resp.status_code == 200
        webhooks = resp.json()
        assert len(webhooks) == 1
        assert webhooks[0]["secret"] == "***"  # masked

    def test_get_single_webhook(self, client):
        create_resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "Test",
                "events": ["shift.created"],
            },
        )
        wh_id = create_resp.json()["record"]["id"]
        resp = client.get(f"/api/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"

    def test_get_nonexistent_webhook(self, client):
        resp = client.get("/api/webhooks/999")
        assert resp.status_code == 404

    def test_update_webhook(self, client):
        create_resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "Original",
                "events": ["shift.created"],
            },
        )
        wh_id = create_resp.json()["record"]["id"]

        resp = client.put(
            f"/api/webhooks/{wh_id}",
            json={"name": "Updated", "active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["name"] == "Updated"
        assert resp.json()["record"]["active"] is False

    def test_delete_webhook(self, client):
        create_resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "ToDelete",
                "events": ["shift.created"],
            },
        )
        wh_id = create_resp.json()["record"]["id"]

        resp = client.delete(f"/api/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify it's gone
        list_resp = client.get("/api/webhooks")
        assert len(list_resp.json()) == 0

    def test_delete_nonexistent_webhook(self, client):
        resp = client.delete("/api/webhooks/999")
        assert resp.status_code == 404

    def test_create_invalid_url(self, client):
        resp = client.post(
            "/api/webhooks",
            json={
                "url": "not-a-url",
                "name": "Bad URL",
                "events": ["shift.created"],
            },
        )
        assert resp.status_code == 422

    def test_create_invalid_events(self, client):
        resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "Bad Events",
                "events": ["invalid.event"],
            },
        )
        assert resp.status_code == 422

    def test_create_empty_events(self, client):
        resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "No Events",
                "events": [],
            },
        )
        assert resp.status_code == 422

    def test_list_events(self, client):
        resp = client.get("/api/webhooks/events/list")
        assert resp.status_code == 200
        assert resp.json()["events"] == VALID_EVENTS


class TestTestEndpoint:
    @pytest.mark.asyncio
    def test_test_webhook_endpoint(self, client):
        # Create webhook first
        create_resp = client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "name": "TestHook",
                "events": ["shift.created"],
            },
        )
        wh_id = create_resp.json()["record"]["id"]

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("api.routers.webhooks.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(f"/api/webhooks/{wh_id}/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["delivery"]["success"] is True
