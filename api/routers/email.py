"""Email administration endpoints for OpenSchichtplaner5.

Provides:
  GET  /api/email/config   – view current SMTP config (no password)
  POST /api/email/test     – send a test email
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..dependencies import limiter, require_admin

router = APIRouter(tags=["Email"])


class TestEmailRequest(BaseModel):
    """Request body for sending a test email."""

    to: str = Field(..., description="Recipient email address", max_length=254)


@router.get(
    "/api/email/config",
    summary="Get email configuration",
    description="Returns the current SMTP configuration (without password). Admin only.",
)
def get_email_config(_cur_user: dict = Depends(require_admin)):
    """Return the current SMTP configuration (safe, no password)."""
    from sp5lib.email_service import get_config

    cfg = get_config()
    return cfg.to_safe_dict()


@router.post(
    "/api/email/test",
    summary="Send test email",
    description=(
        "Send a test email to the given address to verify SMTP configuration. "
        "Returns success/failure status.\n\n**Required role:** Admin"
    ),
)
@limiter.limit("3/minute")
def send_test_email(request: Request, body: TestEmailRequest, _cur_user: dict = Depends(require_admin)):
    """Send a test email to verify SMTP configuration."""
    from sp5lib.email_service import get_config, send_email

    cfg = get_config()
    if not cfg.is_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                "E-Mail ist nicht konfiguriert. "
                "Bitte SP5_SMTP_HOST und SP5_SMTP_USER in .env setzen."
            ),
        )

    ok = send_email(
        to=body.to,
        subject="[SP5] Test-E-Mail",
        title="Test-E-Mail",
        message=(
            "Diese E-Mail wurde erfolgreich über OpenSchichtplaner5 versendet.\n\n"
            "Die SMTP-Konfiguration funktioniert korrekt. ✅"
        ),
        config=cfg,
    )
    if ok:
        return {"ok": True, "message": f"Test-E-Mail an {body.to} gesendet."}
    raise HTTPException(
        status_code=500,
        detail="E-Mail konnte nicht gesendet werden. Bitte SMTP-Einstellungen prüfen.",
    )
