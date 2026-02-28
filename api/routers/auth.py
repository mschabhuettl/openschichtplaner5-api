"""Auth and user management router."""
import time as _time
import secrets
from fastapi import APIRouter, HTTPException, Header, Depends, Request
from pydantic import BaseModel
from typing import Optional
from ..dependencies import (
    get_db, require_admin, _sanitize_500, _logger, _sessions, _failed_logins, _LOCKOUT_WINDOW,
    _LOCKOUT_MAX, _TOKEN_EXPIRE_HOURS, limiter, invalidate_sessions_for_user,
)

router = APIRouter()



@router.get("/api/users", tags=["Users"], summary="List users", description="Return all API users. Requires Admin role.")
def get_users(_admin: dict = Depends(require_admin)):
    return get_db().get_users()


# ── User Management (CRUD) ───────────────────────────────────

class UserCreate(BaseModel):
    NAME: str
    DESCRIP: Optional[str] = ''
    PASSWORD: str
    role: str = 'Leser'   # Admin | Planer | Leser


class UserUpdate(BaseModel):
    NAME: Optional[str] = None
    DESCRIP: Optional[str] = None
    PASSWORD: Optional[str] = None
    role: Optional[str] = None   # Admin | Planer | Leser


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/users", tags=["Users"], summary="Create user", description="Create a new API user. Requires Admin role.")
def create_user(body: UserCreate, _admin: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if not body.PASSWORD or not body.PASSWORD.strip():
        raise HTTPException(status_code=400, detail="Feld 'PASSWORD' darf nicht leer sein")
    if body.role not in ('Admin', 'Planer', 'Leser'):
        raise HTTPException(status_code=400, detail="role muss Admin, Planer oder Leser sein")
    try:
        result = get_db().create_user(body.model_dump())
        _logger.warning(
            "AUDIT USER_CREATE | admin=%s new_user=%s role=%s",
            _admin.get('NAME'), body.NAME, body.role
        )
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith('DUPLICATE:USERNAME:'):
            raise HTTPException(status_code=409, detail=f"Benutzername '{body.NAME}' existiert bereits")
        raise _sanitize_500(e, 'create_user')
    except Exception as e:
        raise _sanitize_500(e, 'create_user')


@router.put("/api/users/{user_id}", tags=["Users"], summary="Update user", description="Update an existing API user. Requires Admin role.")
def update_user(user_id: int, body: UserUpdate, _admin: dict = Depends(require_admin)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if 'role' in data and data['role'] not in ('Admin', 'Planer', 'Leser'):
        raise HTTPException(status_code=400, detail="role muss Admin, Planer oder Leser sein")
    try:
        result = get_db().update_user(user_id, data)
        _logger.warning(
            "AUDIT USER_UPDATE | admin=%s target_id=%d fields=%s",
            _admin.get('NAME'), user_id, list(data.keys())
        )
        return {"ok": True, "record": result}
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Benutzer ID {user_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_user/{user_id}')


@router.delete("/api/users/{user_id}", tags=["Users"], summary="Delete user", description="Soft-delete (hide) an API user. Requires Admin role.")
def delete_user(user_id: int, _admin: dict = Depends(require_admin)):
    try:
        count = get_db().delete_user(user_id)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"Benutzer ID {user_id} nicht gefunden")
        removed = invalidate_sessions_for_user(user_id)
        _logger.warning(
            "AUDIT USER_DELETE | admin=%s target_id=%d sessions_revoked=%d",
            _admin.get('NAME'), user_id, removed
        )
        return {"ok": True, "hidden": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f'delete_user/{user_id}')


class ChangePasswordBody(BaseModel):
    new_password: str


@router.post("/api/users/{user_id}/change-password", tags=["Users"], summary="Change user password", description="Set a new password for an API user. Requires Admin role.")
def change_user_password(user_id: int, body: ChangePasswordBody, _admin: dict = Depends(require_admin)):
    if not body.new_password or len(body.new_password.strip()) < 1:
        raise HTTPException(status_code=400, detail="Passwort darf nicht leer sein")
    try:
        ok = get_db().change_password(user_id, body.new_password)
        if not ok:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        # Invalidate all existing sessions for this user (token rotation on pw change)
        removed = invalidate_sessions_for_user(user_id)
        _logger.warning(
            "AUDIT PASSWORD_CHANGE | admin=%s target_id=%d sessions_revoked=%d",
            _admin.get('NAME'), user_id, removed
        )
        return {"ok": True, "sessions_revoked": removed}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.post("/api/auth/login", tags=["Auth"], summary="Login", description="Authenticate with username and password. Returns a session token valid for 8 hours (configurable via TOKEN_EXPIRE_HOURS).")
@limiter.limit("5/minute")
def login(request: Request, body: LoginBody):
    """Simple login: verify username+password against 5USER.DBF."""
    client_ip = request.client.host if request.client else 'unknown'
    now = _time.time()
    username = body.username

    # ── Brute-force check ──────────────────────────────────────
    # Purge old entries (>15min) then check lockout
    timestamps = _failed_logins.get(username, [])
    timestamps = [t for t in timestamps if now - t < _LOCKOUT_WINDOW]
    _failed_logins[username] = timestamps
    if len(timestamps) >= _LOCKOUT_MAX:
        _logger.warning(
            "AUTH LOCKOUT | ip=%s username=%s attempts=%d", client_ip, username, len(timestamps)
        )
        raise HTTPException(
            status_code=429,
            detail="Zu viele Fehlversuche. Bitte 15 Minuten warten."
        )

    user = get_db().verify_user_password(username, body.password)
    if user is None:
        _failed_logins[username] = timestamps + [now]
        _logger.warning(
            "AUTH LOGIN_FAIL | ip=%s username=%s", client_ip, username
        )
        raise HTTPException(status_code=401, detail="Ungültiger Benutzername oder Passwort")

    # Successful login: clear failed attempts
    _failed_logins.pop(username, None)
    _logger.info("AUTH LOGIN_OK | ip=%s username=%s", client_ip, username)

    # Generate a session token with expiry
    token = secrets.token_hex(32)
    expires_at = now + _TOKEN_EXPIRE_HOURS * 3600
    _sessions[token] = {**user, 'expires_at': expires_at}
    return {
        "ok": True,
        "token": token,
        "user": user,
        "expires_at": expires_at,
    }


@router.post("/api/auth/logout", tags=["Auth"], summary="Logout", description="Invalidate the current session token.")
def logout(x_auth_token: Optional[str] = Header(None)):
    """Invalidate the session token."""
    if x_auth_token and x_auth_token in _sessions:
        del _sessions[x_auth_token]
    return {"ok": True}
