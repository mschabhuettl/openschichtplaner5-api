"""Webhook router: CRUD + delivery + test endpoint."""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC
from datetime import datetime as _dt

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..dependencies import _logger, require_admin

router = APIRouter()

# ── Storage (JSON file, same pattern as frontend_errors) ────────
_WEBHOOKS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "webhooks.json"
)

VALID_EVENTS = [
    "shift.created",
    "shift.updated",
    "shift.deleted",
    "absence.created",
    "absence.approved",
]


def _load_webhooks() -> list[dict]:
    os.makedirs(os.path.dirname(_WEBHOOKS_FILE), exist_ok=True)
    if not os.path.exists(_WEBHOOKS_FILE):
        return []
    with open(_WEBHOOKS_FILE, encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []


def _save_webhooks(webhooks: list[dict]) -> None:
    os.makedirs(os.path.dirname(_WEBHOOKS_FILE), exist_ok=True)
    with open(_WEBHOOKS_FILE, "w", encoding="utf-8") as f:
        json.dump(webhooks, f, ensure_ascii=False, indent=2)


def _next_id(webhooks: list[dict]) -> int:
    if not webhooks:
        return 1
    return max(w.get("id", 0) for w in webhooks) + 1


# ── Pydantic models ─────────────────────────────────────────────


class WebhookCreate(BaseModel):
    url: str = Field(..., max_length=2000)
    name: str = Field(..., min_length=1, max_length=200)
    events: list[str] = Field(..., min_length=1)
    active: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL muss mit http:// oder https:// beginnen")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        invalid = [e for e in v if e not in VALID_EVENTS]
        if invalid:
            raise ValueError(
                f"Ungültige Events: {', '.join(invalid)}. "
                f"Erlaubt: {', '.join(VALID_EVENTS)}"
            )
        return v


class WebhookUpdate(BaseModel):
    url: str | None = Field(None, max_length=2000)
    name: str | None = Field(None, min_length=1, max_length=200)
    events: list[str] | None = None
    active: bool | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("URL muss mit http:// oder https:// beginnen")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            invalid = [e for e in v if e not in VALID_EVENTS]
            if invalid:
                raise ValueError(
                    f"Ungültige Events: {', '.join(invalid)}. "
                    f"Erlaubt: {', '.join(VALID_EVENTS)}"
                )
        return v


# ── HMAC Signing ─────────────────────────────────────────────────


def sign_payload(secret: str, payload: bytes) -> str:
    """Create HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


# ── Delivery Logic ───────────────────────────────────────────────

MAX_RETRIES = 3
BACKOFF_MS = 500


async def deliver_webhook(webhook: dict, event: str, data: dict) -> dict:
    """Deliver a webhook event with retry logic. Returns delivery result."""
    payload = json.dumps(
        {
            "event": event,
            "timestamp": _dt.now(UTC).isoformat(),
            "data": data,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    signature = sign_payload(webhook["secret"], payload)
    headers = {
        "Content-Type": "application/json",
        "X-SP5-Signature": signature,
        "X-SP5-Event": event,
    }

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook["url"],
                    content=payload,
                    headers=headers,
                )
                if response.status_code < 300:
                    return {
                        "success": True,
                        "status_code": response.status_code,
                        "attempt": attempt + 1,
                        "timestamp": _dt.now(UTC).isoformat(),
                    }
                last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = str(exc)

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(BACKOFF_MS / 1000 * (attempt + 1))

    return {
        "success": False,
        "error": last_error,
        "attempt": MAX_RETRIES,
        "timestamp": _dt.now(UTC).isoformat(),
    }


async def dispatch_event(event: str, data: dict) -> None:
    """Dispatch an event to all active webhooks subscribed to it."""
    webhooks = _load_webhooks()
    for webhook in webhooks:
        if not webhook.get("active", False):
            continue
        if event not in webhook.get("events", []):
            continue
        try:
            result = await deliver_webhook(webhook, event, data)
            # Update last delivery status
            webhook["last_delivery"] = result
            _logger.info(
                "Webhook delivery: id=%d name=%s event=%s success=%s",
                webhook["id"],
                webhook["name"],
                event,
                result["success"],
            )
        except Exception as exc:
            webhook["last_delivery"] = {
                "success": False,
                "error": str(exc),
                "timestamp": _dt.now(UTC).isoformat(),
            }
            _logger.error(
                "Webhook delivery failed: id=%d error=%s", webhook["id"], exc
            )
    _save_webhooks(webhooks)


# ── CRUD Endpoints ───────────────────────────────────────────────


@router.get(
    "/api/webhooks",
    tags=["Webhooks"],
    summary="List all webhooks",
    description="Return all configured webhooks. Admin only.",
)
def list_webhooks(_admin: dict = Depends(require_admin)) -> list[dict]:
    webhooks = _load_webhooks()
    # Don't expose secrets in list view
    result = []
    for w in webhooks:
        safe = {**w}
        safe["secret"] = "***" if w.get("secret") else ""
        result.append(safe)
    return result


@router.get(
    "/api/webhooks/{webhook_id}",
    tags=["Webhooks"],
    summary="Get webhook by ID",
    description="Return a single webhook by ID. Admin only.",
)
def get_webhook(webhook_id: int, _admin: dict = Depends(require_admin)) -> dict:
    webhooks = _load_webhooks()
    for w in webhooks:
        if w["id"] == webhook_id:
            safe = {**w}
            safe["secret"] = "***" if w.get("secret") else ""
            return safe
    raise HTTPException(status_code=404, detail="Webhook nicht gefunden")


@router.post(
    "/api/webhooks",
    tags=["Webhooks"],
    summary="Create a new webhook",
    description="Create a new webhook subscription. A signing secret is auto-generated. Admin only.",
)
def create_webhook(
    body: WebhookCreate, _admin: dict = Depends(require_admin)
) -> dict:
    webhooks = _load_webhooks()
    new_webhook = {
        "id": _next_id(webhooks),
        "url": body.url,
        "name": body.name,
        "events": body.events,
        "secret": secrets.token_hex(32),
        "active": body.active,
        "created_at": _dt.now(UTC).isoformat(),
        "last_delivery": None,
    }
    webhooks.append(new_webhook)
    _save_webhooks(webhooks)
    _logger.info(
        "Webhook created: id=%d name=%s url=%s",
        new_webhook["id"],
        new_webhook["name"],
        new_webhook["url"],
    )
    return {"ok": True, "record": new_webhook}


@router.put(
    "/api/webhooks/{webhook_id}",
    tags=["Webhooks"],
    summary="Update a webhook",
    description="Update an existing webhook. Admin only.",
)
def update_webhook(
    webhook_id: int,
    body: WebhookUpdate,
    _admin: dict = Depends(require_admin),
) -> dict:
    webhooks = _load_webhooks()
    for w in webhooks:
        if w["id"] == webhook_id:
            if body.url is not None:
                w["url"] = body.url
            if body.name is not None:
                w["name"] = body.name
            if body.events is not None:
                w["events"] = body.events
            if body.active is not None:
                w["active"] = body.active
            _save_webhooks(webhooks)
            _logger.info("Webhook updated: id=%d", webhook_id)
            return {"ok": True, "record": w}
    raise HTTPException(status_code=404, detail="Webhook nicht gefunden")


@router.delete(
    "/api/webhooks/{webhook_id}",
    tags=["Webhooks"],
    summary="Delete a webhook",
    description="Delete a webhook subscription. Admin only.",
)
def delete_webhook(
    webhook_id: int, _admin: dict = Depends(require_admin)
) -> dict:
    webhooks = _load_webhooks()
    original_len = len(webhooks)
    webhooks = [w for w in webhooks if w["id"] != webhook_id]
    if len(webhooks) == original_len:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")
    _save_webhooks(webhooks)
    _logger.info("Webhook deleted: id=%d", webhook_id)
    return {"ok": True, "deleted": webhook_id}


@router.post(
    "/api/webhooks/{webhook_id}/test",
    tags=["Webhooks"],
    summary="Send test event to webhook",
    description="Send a test event payload to the webhook URL. Admin only.",
)
async def test_webhook(
    webhook_id: int, _admin: dict = Depends(require_admin)
) -> dict:
    webhooks = _load_webhooks()
    webhook = None
    for w in webhooks:
        if w["id"] == webhook_id:
            webhook = w
            break
    if webhook is None:
        raise HTTPException(status_code=404, detail="Webhook nicht gefunden")

    test_data = {
        "message": "Dies ist ein Test-Event von OpenSchichtplaner5",
        "webhook_id": webhook_id,
        "webhook_name": webhook["name"],
    }

    result = await deliver_webhook(webhook, "test", test_data)

    # Update last delivery
    webhook["last_delivery"] = result
    _save_webhooks(webhooks)

    return {"ok": result["success"], "delivery": result}


@router.get(
    "/api/webhooks/events/list",
    tags=["Webhooks"],
    summary="List available webhook events",
    description="Return all available webhook event types.",
)
def list_events(_admin: dict = Depends(require_admin)) -> dict:
    return {"events": VALID_EVENTS}
