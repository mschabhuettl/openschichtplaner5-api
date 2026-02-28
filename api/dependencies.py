"""
Shared dependencies for OpenSchichtplaner5 API.
Extracted from main.py for modular router support.
"""
import os
import logging
import logging.handlers
import time as _time
import traceback

from fastapi import HTTPException, Header, Depends, Request
from typing import Optional
from sp5lib.database import SP5Database
from slowapi import Limiter
from slowapi.util import get_remote_address

# ── Structured JSON Logging setup ───────────────────────────────
import json as _json
from datetime import datetime as _dt, timezone as _tz

class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": _dt.fromtimestamp(record.created, tz=_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return _json.dumps(entry, ensure_ascii=False)

_log_file = '/tmp/sp5-api.log'
_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=10 * 1024 * 1024, backupCount=3
)
_handler.setFormatter(_JsonFormatter())

_logger = logging.getLogger('sp5api')
# Log level configurable via ENV
_log_level_str = os.environ.get('SP5_LOG_LEVEL', 'INFO').upper()
_log_level = getattr(logging, _log_level_str, logging.INFO)
_logger.setLevel(_log_level)
_logger.addHandler(_handler)
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(_JsonFormatter())
_logger.addHandler(_stderr_handler)

# Keep reference to log file path for health endpoint
SP5_LOG_FILE = _log_file

# ── Rate Limiter ─────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ── Session store ────────────────────────────────────────────────
# NOTE: In-process dict — not safe for multi-worker deployments.
_sessions: dict[str, dict] = {}

# Token lifetime
_TOKEN_EXPIRE_HOURS = float(os.environ.get('TOKEN_EXPIRE_HOURS', '8'))

# Max concurrent sessions per user (prevents session flooding)
_MAX_SESSIONS_PER_USER = int(os.environ.get('MAX_SESSIONS_PER_USER', '10'))

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


def get_current_user(
    request: Request,
    x_auth_token: Optional[str] = Header(None),
) -> Optional[dict]:
    """Return user dict for the given token, or None.

    Reads from X-Auth-Token header first; falls back to ?token= query param
    for SSE connections where EventSource cannot set custom headers.
    """
    token = x_auth_token or request.query_params.get('token')
    if token and _is_token_valid(token):
        return _sessions[token]
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


def invalidate_sessions_for_user(user_id: int) -> int:
    """Remove all active sessions for a given user ID. Returns count removed."""
    to_remove = [tok for tok, s in _sessions.items() if s.get('ID') == user_id]
    for tok in to_remove:
        del _sessions[tok]
    return len(to_remove)


def purge_expired_sessions() -> int:
    """Remove all expired sessions from the in-memory store. Returns count removed."""
    now = _time.time()
    to_remove = [
        tok for tok, s in list(_sessions.items())
        if s.get('expires_at') is not None and now > s['expires_at']
    ]
    for tok in to_remove:
        _sessions.pop(tok, None)
    return len(to_remove)


def purge_stale_failed_logins() -> int:
    """Remove username entries whose timestamps have all expired. Returns count removed."""
    now = _time.time()
    stale = [
        uname for uname, timestamps in list(_failed_logins.items())
        if not any(now - t < _LOCKOUT_WINDOW for t in timestamps)
    ]
    for uname in stale:
        _failed_logins.pop(uname, None)
    return len(stale)


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
