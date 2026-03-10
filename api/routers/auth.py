"""Auth and user management router."""

import os
import re as _re
import time as _time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ..dependencies import (
    _LOCKOUT_MAX,
    _LOCKOUT_WINDOW,
    _MAX_SESSIONS_PER_USER,
    _TOKEN_EXPIRE_HOURS,
    _failed_logins,
    _logger,
    _sanitize_500,
    _sessions,
    create_jwt_token,
    get_db,
    invalidate_sessions_for_user,
    limiter,
    require_admin,
    require_auth,
    require_planer,
    write_audit_log,
)

# ── Password strength config (env-configurable) ──────────────────
_PW_MIN_LENGTH = int(os.environ.get("SP5_PW_MIN_LENGTH", "8"))
_PW_REQUIRE_UPPER = os.environ.get("SP5_PW_REQUIRE_UPPER", "true").lower() not in (
    "0",
    "false",
    "no",
)
_PW_REQUIRE_DIGIT = os.environ.get("SP5_PW_REQUIRE_DIGIT", "true").lower() not in (
    "0",
    "false",
    "no",
)


def _validate_password_strength(password: str) -> None:
    """Raise HTTPException 400 if the password does not meet strength requirements."""
    if len(password) < _PW_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Passwort muss mindestens {_PW_MIN_LENGTH} Zeichen lang sein.",
        )
    if _PW_REQUIRE_UPPER and not _re.search(r"[A-Z]", password):
        raise HTTPException(
            status_code=400,
            detail="Passwort muss mindestens einen Großbuchstaben enthalten.",
        )
    if _PW_REQUIRE_DIGIT and not _re.search(r"[0-9]", password):
        raise HTTPException(
            status_code=400,
            detail="Passwort muss mindestens eine Ziffer enthalten.",
        )


_IS_DEV = os.environ.get("SP5_DEV_MODE", "").lower() in ("1", "true", "yes")
_COOKIE_NAME = "sp5_token"

router = APIRouter()


@router.get(
    "/api/users",
    tags=["Users"],
    summary="List users",
    description="Return all API users. Requires Admin role.",
)
def get_users(_admin: dict = Depends(require_admin)):
    return get_db().get_users()


# ── User Management (CRUD) ───────────────────────────────────


class UserCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    DESCRIP: str | None = Field("", max_length=500)
    PASSWORD: str = Field(..., min_length=6, max_length=200)
    role: str = Field("Leser", pattern=r"^(Admin|Planer|Leser)$")

    @field_validator("NAME")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Benutzername darf nicht nur aus Leerzeichen bestehen")
        return v.strip()


class UserUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    DESCRIP: str | None = Field(None, max_length=500)
    PASSWORD: str | None = Field(None, min_length=6, max_length=200)
    role: str | None = Field(None, pattern=r"^(Admin|Planer|Leser)$")

    @field_validator("NAME")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Benutzername darf nicht nur aus Leerzeichen bestehen")
        return v.strip() if v is not None else v


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)
    totp_code: str | None = Field(None, max_length=20)


@router.post(
    "/api/users",
    tags=["Users"],
    summary="Create user",
    description="Create a new API user. Requires Admin role.",
)
def create_user(body: UserCreate, _admin: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if not body.PASSWORD or not body.PASSWORD.strip():
        raise HTTPException(
            status_code=400, detail="Feld 'PASSWORD' darf nicht leer sein"
        )
    if body.role not in ("Admin", "Planer", "Leser"):
        raise HTTPException(
            status_code=400, detail="role muss Admin, Planer oder Leser sein"
        )
    _validate_password_strength(body.PASSWORD)
    try:
        result = get_db().create_user(body.model_dump())
        _logger.warning(
            "AUDIT USER_CREATE | admin=%s new_user=%s role=%s",
            _admin.get("NAME"),
            body.NAME,
            body.role,
        )
        write_audit_log(
            "USER_CREATE",
            _admin.get("NAME", "?"),
            {
                "new_user": body.NAME,
                "role": body.role,
            },
        )
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith("DUPLICATE:USERNAME:"):
            raise HTTPException(
                status_code=409, detail=f"Benutzername '{body.NAME}' existiert bereits"
            )
        raise _sanitize_500(e, "create_user")
    except Exception as e:
        raise _sanitize_500(e, "create_user")


@router.put(
    "/api/users/{user_id}",
    tags=["Users"],
    summary="Update user",
    description="Update an existing API user. Requires Admin role.",
)
def update_user(user_id: int, body: UserUpdate, _admin: dict = Depends(require_admin)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "role" in data and data["role"] not in ("Admin", "Planer", "Leser"):
        raise HTTPException(
            status_code=400, detail="role muss Admin, Planer oder Leser sein"
        )
    if "PASSWORD" in data:
        _validate_password_strength(data["PASSWORD"])
    try:
        result = get_db().update_user(user_id, data)
        _logger.warning(
            "AUDIT USER_UPDATE | admin=%s target_id=%d fields=%s",
            _admin.get("NAME"),
            user_id,
            list(data.keys()),
        )
        write_audit_log(
            "USER_UPDATE",
            _admin.get("NAME", "?"),
            {
                "target_id": user_id,
                "fields": list(data.keys()),
            },
        )
        return {"ok": True, "record": result}
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Benutzer ID {user_id} nicht gefunden"
        )
    except Exception as e:
        raise _sanitize_500(e, f"update_user/{user_id}")


@router.delete(
    "/api/users/{user_id}",
    tags=["Users"],
    summary="Delete user",
    description="Soft-delete (hide) an API user. Requires Admin role.",
)
def delete_user(user_id: int, _admin: dict = Depends(require_admin)):
    try:
        count = get_db().delete_user(user_id)
        if count == 0:
            raise HTTPException(
                status_code=404, detail=f"Benutzer ID {user_id} nicht gefunden"
            )
        removed = invalidate_sessions_for_user(user_id)
        _logger.warning(
            "AUDIT USER_DELETE | admin=%s target_id=%d sessions_revoked=%d",
            _admin.get("NAME"),
            user_id,
            removed,
        )
        write_audit_log(
            "USER_DELETE",
            _admin.get("NAME", "?"),
            {
                "target_id": user_id,
                "sessions_revoked": removed,
            },
        )
        return {"ok": True, "hidden": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f"delete_user/{user_id}")


class ChangePasswordBody(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=200)


@router.post(
    "/api/users/{user_id}/change-password",
    tags=["Users"],
    summary="Change user password",
    description="Set a new password for an API user. Requires Admin role.",
)
@limiter.limit("5/minute")
def change_user_password(
    request: Request, user_id: int, body: ChangePasswordBody, _admin: dict = Depends(require_admin)
):
    if not body.new_password or len(body.new_password.strip()) < 1:
        raise HTTPException(status_code=400, detail="Passwort darf nicht leer sein")
    _validate_password_strength(body.new_password)
    try:
        ok = get_db().change_password(user_id, body.new_password)
        if not ok:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        # Invalidate all existing sessions for this user (token rotation on pw change)
        removed = invalidate_sessions_for_user(user_id)
        _logger.warning(
            "AUDIT PASSWORD_CHANGE | admin=%s target_id=%d sessions_revoked=%d",
            _admin.get("NAME"),
            user_id,
            removed,
        )
        write_audit_log(
            "PASSWORD_CHANGE",
            _admin.get("NAME", "?"),
            {
                "target_id": user_id,
                "sessions_revoked": removed,
            },
        )
        return {"ok": True, "sessions_revoked": removed}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


# ── Self-service password change (any authenticated user) ─────


class SelfChangePasswordBody(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=6, max_length=200)


@router.post(
    "/api/auth/change-password",
    tags=["Auth"],
    summary="Change own password",
    description="Change the current user's password. Requires the old password for verification.",
)
@limiter.limit("5/minute")
def change_own_password(request: Request, body: SelfChangePasswordBody, user: dict = Depends(require_auth)):
    """Self-service password change: verify old password, set new one."""
    username = user.get("NAME", "")
    user_id = user.get("ID")

    # Verify old password
    db = get_db()
    verified = db.verify_user_password(username, body.old_password)
    if verified is None:
        raise HTTPException(status_code=403, detail="Altes Passwort ist falsch")

    _validate_password_strength(body.new_password)

    try:
        ok = db.change_password(user_id, body.new_password)
        if not ok:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        # Invalidate all OTHER sessions (keep current)
        removed = invalidate_sessions_for_user(user_id)
        _logger.warning(
            "AUDIT PASSWORD_SELF_CHANGE | user=%s user_id=%d sessions_revoked=%d",
            username,
            user_id,
            removed,
        )
        write_audit_log(
            "PASSWORD_SELF_CHANGE",
            username,
            {"user_id": user_id, "sessions_revoked": removed},
        )
        return {"ok": True, "sessions_revoked": removed}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, "change_own_password")


# ── Admin/Planer password reset (generates temp password) ─────


def _generate_temp_password(length: int = 12) -> str:
    """Generate a temporary password that meets strength requirements."""
    import random
    import string

    # Ensure at least 1 upper, 1 lower, 1 digit
    chars = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
    ]
    remaining = length - len(chars)
    pool = string.ascii_letters + string.digits
    chars.extend(random.choices(pool, k=remaining))
    random.shuffle(chars)
    return "".join(chars)


@router.post(
    "/api/users/{user_id}/reset-password",
    tags=["Users"],
    summary="Reset user password (Admin/Planer)",
    description="Generate a temporary password for a user. Sends email if SMTP is configured and user has a linked employee with an email address. Returns the temp password to the admin.",
)
@limiter.limit("5/minute")
def reset_user_password(request: Request, user_id: int, admin: dict = Depends(require_planer)):
    """Admin/Planer resets a user's password to a generated temporary one."""
    db = get_db()
    temp_pw = _generate_temp_password()

    try:
        ok = db.change_password(user_id, temp_pw)
        if not ok:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        removed = invalidate_sessions_for_user(user_id)

        # Try to find user info for logging and email
        users = db.get_users()
        target_user = next((u for u in users if u.get("ID") == user_id), None)
        target_name = target_user.get("NAME", "?") if target_user else "?"

        _logger.warning(
            "AUDIT PASSWORD_RESET | admin=%s target_id=%d target_name=%s sessions_revoked=%d",
            admin.get("NAME"),
            user_id,
            target_name,
            removed,
        )
        write_audit_log(
            "PASSWORD_RESET",
            admin.get("NAME", "?"),
            {
                "target_id": user_id,
                "target_name": target_name,
                "sessions_revoked": removed,
            },
        )

        # Try to send email with temp password
        email_sent = False
        try:
            from sp5lib.email_service import get_config, send_email_async

            cfg = get_config()
            if cfg.is_configured:
                # Try to find employee email by matching user name
                employees = db.get_employees()
                employee_email = None
                for emp in employees:
                    emp_name = f"{(emp.get('FIRSTNAME') or '').strip()} {(emp.get('SURNAME') or '').strip()}".strip()
                    if (emp.get("EMAIL") or "").strip() and (
                        emp_name.lower() == target_name.lower()
                        or (emp.get("SURNAME") or "").strip().lower() == target_name.lower()
                    ):
                        employee_email = emp["EMAIL"].strip()
                        break

                if employee_email:
                    send_email_async(
                        to=employee_email,
                        subject="[SP5] Passwort zurückgesetzt",
                        title="Passwort zurückgesetzt",
                        message=(
                            f"Ihr Passwort wurde von einem Administrator zurückgesetzt.\n\n"
                            f"Ihr neues temporäres Passwort lautet: {temp_pw}\n\n"
                            f"Bitte ändern Sie Ihr Passwort nach dem nächsten Login unter 'Mein Profil'."
                        ),
                        link=f"{cfg.app_url}/mein-profil",
                    )
                    email_sent = True
        except Exception:
            _logger.exception("Failed to send password reset email")

        return {
            "ok": True,
            "temp_password": temp_pw,
            "sessions_revoked": removed,
            "email_sent": email_sent,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f"reset_user_password/{user_id}")


@router.post(
    "/api/auth/login",
    tags=["Auth"],
    summary="Login",
    description="Authenticate with username and password. Returns a session token valid for 8 hours (configurable via TOKEN_EXPIRE_HOURS).",
)
@limiter.limit("5/minute")
def login(request: Request, body: LoginBody):
    """Simple login: verify username+password against 5USER.DBF."""
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    username = body.username

    # ── Brute-force check ──────────────────────────────────────
    # Purge old entries (>15min) then check lockout
    timestamps = _failed_logins.get(username, [])
    timestamps = [t for t in timestamps if now - t < _LOCKOUT_WINDOW]
    _failed_logins[username] = timestamps
    if len(timestamps) >= _LOCKOUT_MAX:
        _logger.warning(
            "AUTH LOCKOUT | ip=%s username=%s attempts=%d",
            client_ip,
            username,
            len(timestamps),
        )
        raise HTTPException(
            status_code=429, detail="Zu viele Fehlversuche. Bitte 15 Minuten warten."
        )

    user = get_db().verify_user_password(username, body.password)
    if user is None:
        _failed_logins[username] = timestamps + [now]
        _logger.warning("AUTH LOGIN_FAIL | ip=%s username=%s", client_ip, username)
        raise HTTPException(
            status_code=401, detail="Ungültiger Benutzername oder Passwort"
        )

    # Successful password check — now handle 2FA if enabled
    db = get_db()
    user_id = user.get("ID")
    totp_enabled = db.totp_get_status(user_id)

    if totp_enabled:
        if not body.totp_code:
            # Password OK but 2FA required — don't issue token yet
            return JSONResponse(
                content={
                    "ok": False,
                    "requires_2fa": True,
                    "detail": "2FA-Code erforderlich",
                }
            )
        # Verify TOTP code
        if not db.totp_verify(user_id, body.totp_code):
            _failed_logins[username] = timestamps + [now]
            _logger.warning("AUTH 2FA_FAIL | ip=%s username=%s", client_ip, username)
            raise HTTPException(
                status_code=401, detail="Ungültiger 2FA-Code"
            )

    # Successful login: clear failed attempts
    _failed_logins.pop(username, None)
    _logger.info("AUTH LOGIN_OK | ip=%s username=%s 2fa=%s", client_ip, username, totp_enabled)
    write_audit_log("LOGIN_OK", username, {"ip": client_ip, "2fa": totp_enabled})

    # Enforce max concurrent sessions per user (evict oldest if over limit)
    user_sessions = [(tok, s) for tok, s in _sessions.items() if s.get("ID") == user_id]
    if len(user_sessions) >= _MAX_SESSIONS_PER_USER:
        # Sort by expires_at ascending, remove oldest
        user_sessions.sort(key=lambda x: x[1].get("expires_at") or 0)
        for tok, _ in user_sessions[: len(user_sessions) - _MAX_SESSIONS_PER_USER + 1]:
            _sessions.pop(tok, None)
        _logger.warning(
            "AUTH SESSION_LIMIT | username=%s evicted=%d",
            username,
            len(user_sessions) - _MAX_SESSIONS_PER_USER + 1,
        )

    # Generate a signed JWT token with expiry
    expires_at = now + _TOKEN_EXPIRE_HOURS * 3600
    token = create_jwt_token(user, expires_at)

    # Set HttpOnly cookie (Secure only in production)
    response = JSONResponse(
        content={
            "ok": True,
            "token": token,
            "user": user,
            "expires_at": expires_at,
        }
    )
    secure = not _IS_DEV
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        path="/",
        max_age=int(_TOKEN_EXPIRE_HOURS * 3600),
        secure=secure,
    )
    return response


@router.get("/api/auth/me", tags=["Auth"], summary="Current user info", description="Return the current authenticated user's info.")
def me(user: dict = Depends(require_auth)):
    """Return the current authenticated user's info."""
    return {k: v for k, v in user.items() if k != "expires_at"}


@router.post(
    "/api/auth/logout",
    tags=["Auth"],
    summary="Logout",
    description="Invalidate the current session token.",
)
def logout(request: Request, x_auth_token: str | None = Header(None)):
    """Invalidate the session token. Reads from cookie or X-Auth-Token header."""
    from ..dependencies import _decode_jwt

    client_ip = request.client.host if request.client else "unknown"
    # Prefer cookie, fall back to header
    token = request.cookies.get(_COOKIE_NAME) or x_auth_token
    logged_out = False
    if token:
        # Try legacy direct lookup first
        if token in _sessions:
            user_info = _sessions[token]
            username = user_info.get("NAME", "?")
            user_id = user_info.get("ID", "?")
            del _sessions[token]
            logged_out = True
        else:
            # Try JWT decode to find session ID
            payload = _decode_jwt(token)
            if payload:
                session_id = payload.get("sid")
                if session_id and session_id in _sessions:
                    user_info = _sessions[session_id]
                    username = user_info.get("NAME", "?")
                    user_id = user_info.get("ID", "?")
                    del _sessions[session_id]
                    logged_out = True

    if logged_out:
        _logger.info(
            "AUTH LOGOUT | ip=%s username=%s user_id=%s", client_ip, username, user_id
        )
    else:
        _logger.info("AUTH LOGOUT_NO_SESSION | ip=%s", client_ip)
    response = JSONResponse(content={"ok": True})
    # Clear the cookie
    response.delete_cookie(key=_COOKIE_NAME, path="/", samesite="strict")
    return response


# ── 2FA / TOTP Management ─────────────────────────────────────


@router.get(
    "/api/auth/2fa/status",
    tags=["Auth"],
    summary="2FA status",
    description="Check whether TOTP 2FA is enabled for the current user.",
)
def totp_status(user: dict = Depends(require_auth)):
    enabled = get_db().totp_get_status(user["ID"])
    return {"enabled": enabled}


class TotpSetupResponse(BaseModel):
    secret: str
    qr_code: str  # base64-encoded PNG
    otpauth_uri: str


@router.post(
    "/api/auth/2fa/setup",
    tags=["Auth"],
    summary="Start 2FA setup",
    description="Generate a TOTP secret and QR code for the authenticator app.",
)
def totp_setup(user: dict = Depends(require_auth)):
    import base64
    import io

    import pyotp
    import qrcode

    db = get_db()
    secret = db.totp_generate_secret(user["ID"])
    username = user.get("NAME", "User")
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="OpenSchichtplaner5")

    # Generate QR code as base64 PNG
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "secret": secret,
        "qr_code": qr_b64,
        "otpauth_uri": uri,
    }


class TotpVerifyBody(BaseModel):
    code: str = Field(..., min_length=6, max_length=20)


@router.post(
    "/api/auth/2fa/enable",
    tags=["Auth"],
    summary="Enable 2FA",
    description="Verify a TOTP code to confirm setup and enable 2FA. Returns backup codes.",
)
@limiter.limit("10/minute")
def totp_enable(request: Request, body: TotpVerifyBody, user: dict = Depends(require_auth)):
    db = get_db()
    backup_codes = db.totp_enable(user["ID"], body.code)
    if backup_codes is None:
        raise HTTPException(status_code=400, detail="Ungültiger Code. Bitte erneut versuchen.")
    _logger.warning("AUDIT 2FA_ENABLED | user=%s user_id=%d", user.get("NAME"), user["ID"])
    write_audit_log("2FA_ENABLED", user.get("NAME", "?"), {"user_id": user["ID"]})
    return {"ok": True, "backup_codes": backup_codes}


class TotpDisableBody(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)


@router.post(
    "/api/auth/2fa/disable",
    tags=["Auth"],
    summary="Disable 2FA",
    description="Disable TOTP 2FA. Requires password confirmation.",
)
@limiter.limit("5/minute")
def totp_disable(request: Request, body: TotpDisableBody, user: dict = Depends(require_auth)):
    db = get_db()
    # Verify password before allowing disable
    verified = db.verify_user_password(user.get("NAME", ""), body.password)
    if verified is None:
        raise HTTPException(status_code=403, detail="Passwort ist falsch")
    db.totp_disable(user["ID"])
    _logger.warning("AUDIT 2FA_DISABLED | user=%s user_id=%d", user.get("NAME"), user["ID"])
    write_audit_log("2FA_DISABLED", user.get("NAME", "?"), {"user_id": user["ID"]})
    return {"ok": True}


@router.post(
    "/api/auth/2fa/admin-disable/{user_id}",
    tags=["Users"],
    summary="Admin: Disable 2FA for user",
    description="Admin can disable 2FA for any user (e.g. if they lost their device).",
)
def admin_disable_2fa(user_id: int, _admin: dict = Depends(require_admin)):
    db = get_db()
    db.totp_disable(user_id)
    _logger.warning(
        "AUDIT 2FA_ADMIN_DISABLED | admin=%s target_id=%d",
        _admin.get("NAME"),
        user_id,
    )
    write_audit_log(
        "2FA_ADMIN_DISABLED",
        _admin.get("NAME", "?"),
        {"target_id": user_id},
    )
    return {"ok": True}
