"""
Shared dependencies for OpenSchichtplaner5 API.
Extracted from main.py for modular router support.
"""
import os
import sys
import logging
import logging.handlers
import time as _time
import traceback

from fastapi import HTTPException, Header, Depends
from typing import Optional
from sp5lib.database import SP5Database
from slowapi import Limiter
from slowapi.util import get_remote_address

# ── Logging setup ───────────────────────────────────────────────
_log_file = '/tmp/sp5-api.log'
_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=10 * 1024 * 1024, backupCount=3
)
_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s %(name)s %(message)s'
))
_logger = logging.getLogger('sp5api')
_logger.setLevel(logging.INFO)
_logger.addHandler(_handler)
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
_logger.addHandler(_stderr_handler)

# ── Rate Limiter ─────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Session store ────────────────────────────────────────────────
# NOTE: In-process dict — not safe for multi-worker deployments.
_sessions: dict[str, dict] = {}

# Token lifetime
_TOKEN_EXPIRE_HOURS = float(os.environ.get('TOKEN_EXPIRE_HOURS', '8'))

# Brute-force tracking
_failed_logins: dict[str, list] = {}
_LOCKOUT_WINDOW = 15 * 60
_LOCKOUT_MAX = 5

# Role hierarchy
_ROLE_LEVEL = {'Leser': 1, 'Planer': 2, 'Admin': 3}

# Dev-mode token
_DEV_TOKEN = "__dev_mode__"
_DEV_USER = {"ID": 0, "NAME": "Developer", "role": "Admin", "ADMIN": True, "RIGHTS": 255}


def _is_token_valid(token: str) -> bool:
    """Return True if the token exists and has not expired."""
    session = _sessions.get(token)
    if not session:
        return False
    expires_at = session.get('expires_at')
    if expires_at is not None and _time.time() > expires_at:
        del _sessions[token]
        return False
    return True


def get_current_user(x_auth_token: Optional[str] = Header(None)) -> Optional[dict]:
    """Return user dict for the given token, or None."""
    if x_auth_token and _is_token_valid(x_auth_token):
        return _sessions[x_auth_token]
    return None


def require_auth(user: Optional[dict] = Depends(get_current_user)) -> dict:
    """Dependency: requires any authenticated user."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return user


def require_role(min_role: str):
    """Factory: returns a dependency that requires at least min_role."""
    def _dep(user: Optional[dict] = Depends(get_current_user)) -> dict:
        if user is None:
            raise HTTPException(status_code=401, detail="Nicht angemeldet")
        user_level = _ROLE_LEVEL.get(user.get('role', 'Leser'), 1)
        required_level = _ROLE_LEVEL.get(min_role, 3)
        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Mindestrolle '{min_role}' erforderlich (aktuell: '{user.get('role')}')"
            )
        return user
    return _dep


def require_admin(user: Optional[dict] = Depends(get_current_user)) -> dict:
    """Dependency: requires Admin role."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if user.get('role') != 'Admin':
        raise HTTPException(status_code=403, detail="Keine Admin-Berechtigung")
    return user


def require_planer(user: Optional[dict] = Depends(get_current_user)) -> dict:
    """Dependency: requires at least Planer role."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if _ROLE_LEVEL.get(user.get('role', 'Leser'), 1) < 2:
        raise HTTPException(status_code=403, detail="Mindestrolle 'Planer' erforderlich")
    return user


def get_db() -> SP5Database:
    """Get a database connection using the current DB_PATH from main module."""
    import api.main as _main
    return SP5Database(_main.DB_PATH)


def _sanitize_500(e: Exception, context: str = '') -> HTTPException:
    """Log full exception, return sanitized 500."""
    _logger.error(
        "500 error context=%s type=%s msg=%s trace=%s",
        context, type(e).__name__, str(e),
        traceback.format_exc().splitlines()[-1],
    )
    return HTTPException(
        status_code=500,
        detail="Interner Serverfehler. Bitte versuche es erneut.",
    )
