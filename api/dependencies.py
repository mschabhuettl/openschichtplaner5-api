"""
Shared dependencies for OpenSchichtplaner5 API.
Extracted from main.py for modular router support.
"""

# ── Structured JSON Logging setup ───────────────────────────────
import json as _json
import logging
import logging.handlers
import os
import secrets as _secrets
import time as _time
from datetime import UTC
from datetime import datetime as _dt

import jwt as _jwt

from fastapi import Depends, Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sp5lib.database import SP5Database


class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": _dt.fromtimestamp(record.created, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%S."
            )
            + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return _json.dumps(entry, ensure_ascii=False)


_log_file = "/tmp/sp5-api.log"
_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=10 * 1024 * 1024, backupCount=3
)
_handler.setFormatter(_JsonFormatter())

_logger = logging.getLogger("sp5api")
# Log level configurable via ENV
_log_level_str = os.environ.get("SP5_LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_str, logging.INFO)
_logger.setLevel(_log_level)
_logger.addHandler(_handler)
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(_JsonFormatter())
_logger.addHandler(_stderr_handler)

# Keep reference to log file path for health endpoint
SP5_LOG_FILE = _log_file

# ── Rate Limiter ─────────────────────────────────────────────────


def _rate_limit_key(request: Request) -> str:
    """Key function: use authenticated user name if available, else client IP.

    This ensures per-user limits for authenticated endpoints and per-IP
    limits for public endpoints (e.g. login).
    """
    token = (
        request.headers.get("x-auth-token")
        or request.cookies.get("sp5_token")
        or request.query_params.get("token")
    )
    if token:
        session = _get_session_from_token(token)
        if session:
            return f"user:{session.get('NAME', 'unknown')}"
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key, default_limits=["100/minute"])

# ── JWT Configuration ────────────────────────────────────────────
# Secret: use env var or generate a strong random one (persists for process lifetime).
# For multi-worker / restart-safe deployments, set SP5_JWT_SECRET in env.
_JWT_SECRET = os.environ.get("SP5_JWT_SECRET") or _secrets.token_hex(64)
_JWT_ALGORITHM = "HS256"

# ── Session store ────────────────────────────────────────────────
# NOTE: In-process dict — not safe for multi-worker deployments.
# JWT provides integrity + expiry; sessions dict enables server-side revocation.
_sessions: dict[str, dict] = {}

# Token lifetime
_TOKEN_EXPIRE_HOURS = float(os.environ.get("TOKEN_EXPIRE_HOURS", "8"))

# Max concurrent sessions per user (prevents session flooding)
_MAX_SESSIONS_PER_USER = int(os.environ.get("MAX_SESSIONS_PER_USER", "10"))

# Brute-force tracking
_failed_logins: dict[str, list] = {}
_LOCKOUT_WINDOW = 15 * 60
_LOCKOUT_MAX = 5

# Role hierarchy
_ROLE_LEVEL = {"Leser": 1, "Planer": 2, "Admin": 3}

# Dev-mode token
_DEV_TOKEN = "__dev_mode__"
_DEV_USER = {
    "ID": 0,
    "NAME": "Developer",
    "role": "Admin",
    "ADMIN": True,
    "RIGHTS": 255,
}

# Whether dev mode is active (cached at import time)
_DEV_MODE_ACTIVE = os.environ.get("SP5_DEV_MODE", "").lower() in ("1", "true", "yes")


def create_jwt_token(user_data: dict, expires_at: float) -> str:
    """Create a signed JWT token containing user session data."""
    # Generate a unique session ID for server-side revocation
    session_id = _secrets.token_hex(16)
    payload = {
        "sid": session_id,
        "uid": user_data.get("ID"),
        "name": user_data.get("NAME", ""),
        "role": user_data.get("role", "Leser"),
        "exp": int(expires_at),
        "iat": int(_time.time()),
    }
    token = _jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)
    # Register in server-side session store for revocation support
    _sessions[session_id] = {**user_data, "expires_at": expires_at, "_session_id": session_id}
    return token


def _decode_jwt(token: str) -> dict | None:
    """Decode and verify a JWT token. Returns payload or None."""
    try:
        payload = _jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except _jwt.ExpiredSignatureError:
        return None
    except _jwt.InvalidTokenError:
        return None


def _is_token_valid(token: str) -> bool:
    """Return True if the token exists and has not expired.

    Supports both legacy session tokens (direct lookup) and JWT tokens.
    """
    # Legacy: direct session lookup (for dev mode token and backward compat)
    session = _sessions.get(token)
    if session:
        expires_at = session.get("expires_at")
        if expires_at is not None and _time.time() > expires_at:
            del _sessions[token]
            return False
        return True

    # JWT: decode and verify, then check server-side revocation
    payload = _decode_jwt(token)
    if payload is None:
        return False
    session_id = payload.get("sid")
    if session_id and session_id in _sessions:
        session = _sessions[session_id]
        expires_at = session.get("expires_at")
        if expires_at is not None and _time.time() > expires_at:
            del _sessions[session_id]
            return False
        return True
    return False


def _get_session_from_token(token: str) -> dict | None:
    """Resolve a token (legacy or JWT) to its session data."""
    # Legacy direct lookup
    session = _sessions.get(token)
    if session:
        return session
    # JWT decode
    payload = _decode_jwt(token)
    if payload is None:
        return None
    session_id = payload.get("sid")
    if session_id:
        return _sessions.get(session_id)
    return None


def get_current_user(
    request: Request,
    x_auth_token: str | None = Header(None),
) -> dict | None:
    """Return user dict for the given token, or None.

    Priority: X-Auth-Token header → sp5_token cookie → ?token= query param
    (query param kept for SSE connections where EventSource cannot set headers).

    Supports both legacy hex tokens and signed JWT tokens.
    """
    token = (
        x_auth_token
        or request.cookies.get("sp5_token")
        or request.query_params.get("token")
    )
    if not token:
        return None

    if _is_token_valid(token):
        return _get_session_from_token(token)
    return None


def require_auth(user: dict | None = Depends(get_current_user)) -> dict:
    """Dependency: requires any authenticated user."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return user


def require_role(min_role: str):
    """Factory: returns a dependency that requires at least min_role."""

    def _dep(user: dict | None = Depends(get_current_user)) -> dict:
        if user is None:
            raise HTTPException(status_code=401, detail="Nicht angemeldet")
        user_level = _ROLE_LEVEL.get(user.get("role", "Leser"), 1)
        required_level = _ROLE_LEVEL.get(min_role, 3)
        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Mindestrolle '{min_role}' erforderlich (aktuell: '{user.get('role')}')",
            )
        return user

    return _dep


def require_admin(user: dict | None = Depends(get_current_user)) -> dict:
    """Dependency: requires Admin role."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if user.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Keine Admin-Berechtigung")
    return user


def require_planer(user: dict | None = Depends(get_current_user)) -> dict:
    """Dependency: requires at least Planer role."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if _ROLE_LEVEL.get(user.get("role", "Leser"), 1) < 2:
        raise HTTPException(
            status_code=403, detail="Mindestrolle 'Planer' erforderlich"
        )
    return user


def get_db() -> SP5Database:
    """Get a database connection using the current DB_PATH from main module."""
    import api.main as _main

    return SP5Database(_main.DB_PATH)


def invalidate_sessions_for_user(user_id: int) -> int:
    """Remove all active sessions for a given user ID. Returns count removed.

    Works for both legacy token keys and JWT session IDs.
    """
    to_remove = [tok for tok, s in _sessions.items() if s.get("ID") == user_id]
    for tok in to_remove:
        del _sessions[tok]
    return len(to_remove)


def purge_expired_sessions() -> int:
    """Remove all expired sessions from the in-memory store. Returns count removed."""
    now = _time.time()
    to_remove = [
        tok
        for tok, s in list(_sessions.items())
        if s.get("expires_at") is not None and now > s["expires_at"]
    ]
    for tok in to_remove:
        _sessions.pop(tok, None)
    return len(to_remove)


def purge_stale_failed_logins() -> int:
    """Remove username entries whose timestamps have all expired. Returns count removed."""
    now = _time.time()
    stale = [
        uname
        for uname, timestamps in list(_failed_logins.items())
        if not any(now - t < _LOCKOUT_WINDOW for t in timestamps)
    ]
    for uname in stale:
        _failed_logins.pop(uname, None)
    return len(stale)


# ── Audit Log (JSON file) ────────────────────────────────────────
import json as _audit_json  # noqa: E402
import threading as _audit_threading  # noqa: E402

_AUDIT_LOG_FILE = os.environ.get("SP5_AUDIT_LOG", "/tmp/sp5-audit.json")
_audit_lock = _audit_threading.Lock()


def write_audit_log(action: str, actor: str, details: dict) -> None:
    """Append a structured audit event to the audit JSON-lines file.

    Each line is a self-contained JSON object with timestamp, action, actor and details.
    """
    from datetime import datetime as _adt

    entry = {
        "timestamp": _adt.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "action": action,
        "actor": actor,
        **details,
    }
    try:
        with _audit_lock:
            with open(_AUDIT_LOG_FILE, "a", encoding="utf-8") as _f:
                _f.write(_audit_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _ae:
        _logger.warning("Audit log write failed: %s", _ae)


def _sanitize_500(e: Exception, context: str = "") -> HTTPException:
    """Log full exception with traceback, return sanitized 500."""
    _logger.exception(
        "500 error context=%s type=%s msg=%s",
        context,
        type(e).__name__,
        str(e),
    )
    return HTTPException(
        status_code=500,
        detail="Interner Serverfehler. Bitte versuche es erneut.",
    )
