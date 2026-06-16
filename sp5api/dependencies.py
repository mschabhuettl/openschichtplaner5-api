"""
Shared dependencies for OpenSchichtplaner5 API.
Extracted from main.py for modular router support.
"""

# ── Structured JSON Logging setup ───────────────────────────────
import contextvars as _contextvars
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

# ── Request-scoped context (propagated to all log entries) ───────
request_id_ctx: _contextvars.ContextVar[str | None] = _contextvars.ContextVar(
    "request_id", default=None
)


class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects with request_id from context."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": _dt.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach request_id from contextvars (if available)
        rid = request_id_ctx.get(None)
        if rid:
            entry["request_id"] = rid
        # Merge extra fields attached by logger.info("msg", extra={...})
        for key in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "username",
            "event",
            "exc_type",
        ):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return _json.dumps(entry, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    """Human-readable formatter for local development (SP5_LOG_FORMAT=text)."""

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        rid = request_id_ctx.get(None) or getattr(record, "request_id", None)
        rid_part = f" [{rid[:8]}]" if rid else ""
        base = f"{ts} {record.levelname:<5}{rid_part} {record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# Choose formatter based on SP5_LOG_FORMAT env var (default: json)
_log_format = os.environ.get("SP5_LOG_FORMAT", "json").lower()
_formatter = _TextFormatter() if _log_format == "text" else _JsonFormatter()

_DEFAULT_LOG_FILE = "/tmp/sp5-api.log"


def _open_log_handler(path: str) -> tuple[str, logging.handlers.RotatingFileHandler]:
    """Create a rotating file handler for ``path``, creating the parent dir if
    needed. On any failure (unwritable/missing dir) fall back to the default
    ``/tmp`` location so logging — and thus startup — never breaks because of a
    misconfigured LOG_FILE. Returns the (possibly fallback) path + handler."""
    for candidate in (path, _DEFAULT_LOG_FILE):
        try:
            parent = os.path.dirname(candidate)
            if parent:
                os.makedirs(parent, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                candidate, maxBytes=10 * 1024 * 1024, backupCount=3
            )
            return candidate, handler
        except OSError:
            continue
    # Both failed (extremely unlikely) — last resort: a no-op stream handler.
    return path, logging.handlers.RotatingFileHandler(_DEFAULT_LOG_FILE, delay=True)


# LOG_FILE is documented in .env.example; default keeps the previous /tmp path.
_log_file, _handler = _open_log_handler(os.environ.get("LOG_FILE") or _DEFAULT_LOG_FILE)
_handler.setFormatter(_formatter)

_logger = logging.getLogger("sp5.api")
# Log level configurable via ENV. LOG_LEVEL is the variable documented in
# .env.example; SP5_LOG_LEVEL stays supported as an alias.
_log_level_str = (os.environ.get("SP5_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "INFO").upper()
_log_level = getattr(logging, _log_level_str, logging.INFO)
_logger.setLevel(_log_level)
_logger.addHandler(_handler)
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(_formatter)
_logger.addHandler(_stderr_handler)

# Keep reference to log file path for health endpoint
SP5_LOG_FILE = _log_file


def _int_env(name: str, default: int) -> int:
    """Read a non-negative int from the environment, falling back to ``default``
    on missing/invalid values (so a typo can never crash startup)."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _str_env(name: str, default: str) -> str:
    """Read a non-empty string from the environment, falling back to ``default``."""
    value = (os.environ.get(name) or "").strip()
    return value or default


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


# Rate limits configurable via ENV (documented in .env.example). RATE_LIMIT_API
# is the global default; RATE_LIMIT_LOGIN guards credential endpoints (used in
# auth.py decorators).
_API_RATE_LIMIT = _str_env("RATE_LIMIT_API", "100/minute")
_LOGIN_RATE_LIMIT = _str_env("RATE_LIMIT_LOGIN", "5/minute")
limiter = Limiter(key_func=_rate_limit_key, default_limits=[_API_RATE_LIMIT])

# ── JWT Configuration ────────────────────────────────────────────
# Secret: use env var or generate a strong random one (persists for process lifetime).
# For multi-worker / restart-safe deployments, set SECRET_KEY (or SP5_JWT_SECRET) in env.


def _resolve_jwt_secret(env: dict[str, str]) -> tuple[str, str | None]:
    """Resolve the JWT signing secret and an optional operator warning.

    Reads ``SP5_JWT_SECRET`` first, then ``SECRET_KEY`` — the latter is the
    variable documented in `.env.example`/README/DEPLOYMENT and the one
    `start.sh` auto-generates, so it MUST be honoured (otherwise the configured
    secret is silently ignored and tokens are signed with a random per-process
    key). The shipped ``change-me…`` placeholder is treated as unset.

    Returns ``(secret, warning_or_None)``. When no real secret is configured a
    strong random per-process secret is generated — fine for local/dev, but in
    production that silently invalidates sessions on every restart and across
    multiple workers, so a warning is surfaced unless running in dev/debug mode.
    """
    configured = (env.get("SP5_JWT_SECRET") or env.get("SECRET_KEY") or "").strip()
    # The shipped placeholder is not a real secret.
    if configured and not configured.lower().startswith("change-me"):
        return configured, None

    dev_mode = env.get("SP5_DEV_MODE", "").lower() in ("1", "true", "yes")
    debug = env.get("DEBUG", "").lower() in ("1", "true", "yes")
    warning = None
    if not dev_mode and not debug:
        warning = (
            "No real JWT secret configured (SECRET_KEY / SP5_JWT_SECRET unset or still the "
            "placeholder) — using a random per-process secret. Sessions will NOT survive a "
            "restart and are invalid across multiple workers. Set SECRET_KEY to a long random "
            "value (openssl rand -hex 32) in production."
        )
    return _secrets.token_hex(64), warning


_JWT_SECRET, _jwt_secret_warning = _resolve_jwt_secret(dict(os.environ))
if _jwt_secret_warning:
    _logger.warning(_jwt_secret_warning)
_JWT_ALGORITHM = "HS256"

# ── Session store ────────────────────────────────────────────────
# JWT provides integrity + expiry; the server-side session store enables
# revocation and the per-user session limit.
#
# `_sessions` is the in-process dict that has always backed sessions. It stays a
# real dict so existing code/tests that mutate it directly keep working. The
# `SessionStore` abstraction routes all session operations through a backend:
#   - memory (DEFAULT): wraps THIS dict by reference → byte-identical behaviour.
#   - redis (opt-in via SP5_SESSION_BACKEND=redis): shared across workers.
# See sp5api/session_store.py.
from sp5api.session_store import MemorySessionStore, create_session_store  # noqa: E402

_sessions: dict[str, dict] = {}
_session_store = create_session_store(_sessions)

# Token lifetime
_TOKEN_EXPIRE_HOURS = float(os.environ.get("TOKEN_EXPIRE_HOURS", "8"))

# Max concurrent sessions per user (prevents session flooding)
_MAX_SESSIONS_PER_USER = int(os.environ.get("MAX_SESSIONS_PER_USER", "10"))

# Brute-force tracking
_failed_logins: dict[str, list] = {}
# Brute-force lockout, configurable via ENV (documented in .env.example).
_LOCKOUT_WINDOW = _int_env("BRUTE_FORCE_LOCKOUT_MINUTES", 15) * 60
_LOCKOUT_MAX = _int_env("BRUTE_FORCE_MAX_ATTEMPTS", 5)

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
    session_data = {**user_data, "expires_at": expires_at, "_session_id": session_id}
    _session_store.set(session_id, session_data, expires_at)
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

    Supports both legacy session tokens (direct lookup) and JWT tokens. Routes
    through the session store, which honours expiry (purging expired entries).
    """
    # Legacy: direct session lookup (for dev mode token and backward compat)
    if _session_store.get(token) is not None:
        return True

    # JWT: decode and verify, then check server-side revocation
    payload = _decode_jwt(token)
    if payload is None:
        return False
    session_id = payload.get("sid")
    if session_id and _session_store.get(session_id) is not None:
        return True
    return False


def _get_session_from_token(token: str) -> dict | None:
    """Resolve a token (legacy or JWT) to its session data."""
    # Legacy direct lookup
    session = _session_store.get(token)
    if session is not None:
        return session
    # JWT decode
    payload = _decode_jwt(token)
    if payload is None:
        return None
    session_id = payload.get("sid")
    if session_id:
        return _session_store.get(session_id)
    return None


def _bearer_token(authorization: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if isinstance(authorization, str) and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip() or None
    return None


def get_current_user(
    request: Request,
    x_auth_token: str | None = Header(None),
    authorization: str | None = Header(None),
) -> dict | None:
    """Return user dict for the given token, or None.

    Priority: Authorization: Bearer → X-Auth-Token header → sp5_token cookie
    → ?token= query param (query param kept for SSE connections where
    EventSource cannot set headers). The token issued by ``/api/auth/login`` is
    therefore usable both as an HttpOnly cookie (the SPA) and as a standard
    Bearer token (API clients).

    Supports both legacy hex tokens and signed JWT tokens.
    """
    token = (
        _bearer_token(authorization)
        or x_auth_token
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


def absence_visibility_mode(user: dict | None = Depends(get_current_user)) -> int:
    """SHOWABS-Modus des aktuellen Benutzers (Spec 9.5.2 Nr. 2.1, D-67):
    0=vollständig, 1=anonymisiert, 2=gar nicht. Admin/anonyme Anfrage ⇒ 0."""
    if user is None or user.get("role") == "Admin":
        return 0
    try:
        mode = int(user.get("SHOWABS_MODE") or 0)
    except (TypeError, ValueError):
        mode = 0
    return mode if mode in (0, 1, 2) else 0


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
        raise HTTPException(status_code=403, detail="Mindestrolle 'Planer' erforderlich")
    return user


# ── Granulare 5USER-Schreibrechte (Spec 9.6, Parity G-1) ─────────


def _has_write_flag(user: dict, flag: str) -> bool:
    """True, wenn das granulare 5USER-Flag dem Benutzer das Schreiben erlaubt.

    Admin-Rolle ⇒ immer True. Fehlt das Flag im Session-Dict (Legacy-
    Sessions, Test-Fixtures), gilt es als erlaubt — die Rollenprüfung
    (mind. Planer) bleibt davon unberührt. Nur ein explizit gesetztes,
    falsy Flag sperrt.
    """
    if user.get("role") == "Admin":
        return True
    val = user.get(flag)
    return True if val is None else bool(val)


def require_write(*flags: str):
    """Factory: mind. Planer-Rolle UND eines der granularen Schreib-Flags
    (z. B. WDUTIES, WABSENCES; Diensttausch: WDUTIES oder WSWAPONLY)."""

    def _dep(user: dict | None = Depends(get_current_user)) -> dict:
        if user is None:
            raise HTTPException(status_code=401, detail="Nicht angemeldet")
        if _ROLE_LEVEL.get(user.get("role", "Leser"), 1) < 2:
            raise HTTPException(
                status_code=403, detail="Mindestrolle 'Planer' erforderlich"
            )
        if not any(_has_write_flag(user, f) for f in flags):
            raise HTTPException(
                status_code=403,
                detail=f"Keine Schreibberechtigung ({'/'.join(flags)})",
            )
        return user

    return _dep


def require_addempl(user: dict | None = Depends(get_current_user)) -> dict:
    """Mitarbeiter anlegen: Admin oder explizites ADDEMPL-Flag.

    ADDEMPL ist laut Spec 9.5.3 Nr. 2.1 ein Opt-in ("neue Mitarbeiter
    erfassen") — anders als die W*-Flags wird es daher nur bei explizit
    gesetztem Flag gewährt.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if user.get("role") == "Admin":
        return user
    if _ROLE_LEVEL.get(user.get("role", "Leser"), 1) >= 2 and bool(
        user.get("ADDEMPL")
    ):
        return user
    raise HTTPException(
        status_code=403,
        detail=(
            "Mitarbeiter anlegen erfordert Admin oder das Recht "
            "'neue Mitarbeiter erfassen' (ADDEMPL)"
        ),
    )


def enforce_wpast(user: dict, *dates: str | None) -> None:
    """Vergangenheits-Schreibschutz (5USER.WPAST, Spec 9.6).

    WPAST explizit 0 ⇒ Schreibzugriffe mit Datum < heute → 403.
    Admin und Sessions ohne Flag sind unbeschränkt.
    """
    if _has_write_flag(user, "WPAST"):
        return
    from datetime import date as _date

    today = _date.today().isoformat()
    for d in dates:
        if d and d < today:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Änderungen in der Vergangenheit sind für diesen "
                    "Benutzer gesperrt (WPAST)"
                ),
            )


def get_db():
    """Get a database connection using the configured backend.

    Returns SP5Database (DBF) or SP5PostgresDatabase (PostgreSQL)
    depending on the DB_BACKEND environment variable.
    """
    from sp5lib.db_config import is_postgresql

    if is_postgresql():
        from sp5lib.db_factory import get_database

        return get_database()
    else:
        import sp5api.main as _main

        return SP5Database(_main.DB_PATH)


def invalidate_sessions_for_user(user_id: int, except_session_id: str | None = None) -> int:
    """Remove all active sessions for a given user ID. Returns count removed.

    Works for both legacy token keys and JWT session IDs. If except_session_id
    is provided, the matching session is preserved (used to keep the caller's
    own session alive on self-service password changes).
    """
    to_remove = [
        sid
        for sid, s in _session_store.sessions_for_user(user_id)
        if s.get("_session_id") != except_session_id
    ]
    for sid in to_remove:
        _session_store.delete(sid)
    return len(to_remove)


def purge_expired_sessions() -> int:
    """Remove all expired sessions from the in-memory store. Returns count removed.

    Only meaningful for the memory backend; with the redis backend, key TTLs
    let Redis evict expired sessions on its own, so there is nothing to purge.
    """
    if not isinstance(_session_store, MemorySessionStore):
        return 0
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


import errno as _errno  # noqa: E402


def describe_write_error(exc: BaseException) -> tuple[int, str] | None:
    """Map a filesystem/permission error to ``(status_code, user-facing detail)``.

    Returns ``None`` if ``exc`` is not a filesystem error we can explain. Shared by
    ``_sanitize_500`` (handlers that catch) and the global ``OSError`` handler
    (uncaught) so that NO write failure ever ends as an opaque 500 — every write
    path either succeeds or returns a clear, specific message + log (cycle 8 /
    Regel 6). The most common real cause is a data directory mounted into the
    non-root container without write permission for the container user.
    """
    if not isinstance(exc, OSError):
        return None
    eno = getattr(exc, "errno", None)
    fname = getattr(exc, "filename", None)
    where = f" ({fname})" if fname else ""
    if eno in (_errno.EACCES, _errno.EPERM, _errno.EROFS):
        return 503, (
            "Das Daten-Verzeichnis ist nicht beschreibbar — der Schreibvorgang "
            "wurde abgebrochen, es wurde nichts verändert. Der Container-Benutzer "
            "braucht Schreibrechte auf das gemountete Daten-Verzeichnis."
            f" [Errno {eno}: {exc.strerror}{where}]"
        )
    if eno == _errno.ENOSPC:
        return 507, (
            "Kein freier Speicherplatz auf dem Daten-Volume — der Schreibvorgang "
            f"wurde abgebrochen. [Errno {eno}: {exc.strerror}{where}]"
        )
    return None


def _sanitize_500(e: Exception, context: str = "") -> HTTPException:
    """Log full exception with traceback, return a sanitized error.

    Filesystem/permission errors get a clear, specific message (see
    ``describe_write_error``); everything else stays a generic 500.
    """
    _logger.exception(
        "500 error context=%s type=%s msg=%s",
        context,
        type(e).__name__,
        str(e),
    )
    mapped = describe_write_error(e)
    if mapped is not None:
        status, detail = mapped
        return HTTPException(status_code=status, detail=detail)
    return HTTPException(
        status_code=500,
        detail="Interner Serverfehler. Bitte versuche es erneut.",
    )
