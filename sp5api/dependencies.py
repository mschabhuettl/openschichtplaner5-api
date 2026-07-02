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
    """Gibt Log-Einträge als einzeilige JSON-Objekte mit request_id aus dem Kontext aus."""

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
    """Menschenlesbarer Formatter für die lokale Entwicklung (SP5_LOG_FORMAT=text)."""

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
    """Erzeugt einen rotierenden File-Handler für ``path`` (legt das Eltern-
    Verzeichnis bei Bedarf an). Bei jedem Fehler (unbeschreibbar/fehlend)
    Fallback auf den ``/tmp``-Default, damit Logging — und damit der Start —
    nie an einem falsch konfigurierten LOG_FILE scheitert. Liefert den
    (ggf. Fallback-)Pfad + Handler."""
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


# LOG_FILE ist in .env.example dokumentiert; der Default behält den bisherigen /tmp-Pfad.
_log_file, _handler = _open_log_handler(os.environ.get("LOG_FILE") or _DEFAULT_LOG_FILE)
_handler.setFormatter(_formatter)

_logger = logging.getLogger("sp5.api")
# Log-Level per ENV konfigurierbar. LOG_LEVEL ist die in .env.example
# dokumentierte Variable; SP5_LOG_LEVEL bleibt als Alias unterstützt.
_log_level_str = (os.environ.get("SP5_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "INFO").upper()
_log_level = getattr(logging, _log_level_str, logging.INFO)
_logger.setLevel(_log_level)
_logger.addHandler(_handler)
_stderr_handler = logging.StreamHandler()
_stderr_handler.setFormatter(_formatter)
_logger.addHandler(_stderr_handler)

# Referenz auf den Logdatei-Pfad für den Health-Endpunkt behalten
SP5_LOG_FILE = _log_file


def _int_env(name: str, default: int) -> int:
    """Liest einen nicht-negativen Int aus der Umgebung; bei fehlenden/ungültigen
    Werten Fallback auf ``default`` (ein Tippfehler darf den Start nie crashen)."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _str_env(name: str, default: str) -> str:
    """Liest einen nicht-leeren String aus der Umgebung, sonst ``default``."""
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
# ist der globale Default; RATE_LIMIT_LOGIN schützt die Credential-Endpunkte
# (genutzt in den auth.py-Decorators).
_API_RATE_LIMIT = _str_env("RATE_LIMIT_API", "100/minute")
_LOGIN_RATE_LIMIT = _str_env("RATE_LIMIT_LOGIN", "5/minute")
limiter = Limiter(key_func=_rate_limit_key, default_limits=[_API_RATE_LIMIT])

# ── JWT Configuration ────────────────────────────────────────────
# Secret: Env-Variable nutzen oder ein starkes Zufalls-Secret erzeugen (gilt für
# die Prozess-Lebensdauer). Für Multi-Worker-/Restart-feste Deployments
# SECRET_KEY (oder SP5_JWT_SECRET) in der Env setzen.


def _resolve_jwt_secret(env: dict[str, str]) -> tuple[str, str | None]:
    """Löst das JWT-Signatur-Secret auf, plus optionale Betreiber-Warnung.

    Liest zuerst ``SP5_JWT_SECRET``, dann ``SECRET_KEY`` — letzteres ist die in
    `.env.example`/README/DEPLOYMENT dokumentierte Variable, die `start.sh`
    auto-generiert; sie MUSS beachtet werden (sonst wird das konfigurierte
    Secret still ignoriert und Tokens mit einem zufälligen Prozess-Schlüssel
    signiert). Der ausgelieferte ``change-me…``-Platzhalter gilt als ungesetzt.

    Returns ``(secret, warning_or_None)``. When no real secret is configured a
    strong random per-process secret is generated — fine for local/dev, but in
    production that silently invalidates sessions on every restart and across
    multiple workers, so a warning is surfaced unless running in dev/debug mode.
    """
    configured = (env.get("SP5_JWT_SECRET") or env.get("SECRET_KEY") or "").strip()
    # Der ausgelieferte Platzhalter ist kein echtes Secret.
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
# JWT liefert Integrität + Ablauf; der serverseitige Session-Store ermöglicht
# Widerruf und das Sessions-Limit je Benutzer.
#
# `_sessions` ist das In-Prozess-dict, das Sessions schon immer getragen hat.
# Es bleibt ein echtes dict, damit Code/Tests, die es direkt verändern, weiter
# funktionieren. Die `SessionStore`-Abstraktion leitet alle Session-Operationen
# durch ein Backend:
#   - memory (DEFAULT): umhüllt DIESES dict per Referenz → byte-identisches Verhalten.
#   - redis (Opt-in via SP5_SESSION_BACKEND=redis): über Worker geteilt.
# Siehe sp5api/session_store.py.
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

# Ob der Dev-Modus aktiv ist (beim Import gecacht)
_DEV_MODE_ACTIVE = os.environ.get("SP5_DEV_MODE", "").lower() in ("1", "true", "yes")


def create_jwt_token(user_data: dict, expires_at: float) -> str:
    """Create a signed JWT token containing user session data."""
    # Eindeutige Session-ID für den serverseitigen Widerruf erzeugen
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
    # Im serverseitigen Session-Store registrieren (Widerrufs-Unterstützung)
    session_data = {**user_data, "expires_at": expires_at, "_session_id": session_id}
    _session_store.set(session_id, session_data, expires_at)
    return token


def _decode_jwt(token: str) -> dict | None:
    """Dekodiert und verifiziert ein JWT. Liefert die Payload oder None."""
    try:
        payload = _jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except _jwt.ExpiredSignatureError:
        return None
    except _jwt.InvalidTokenError:
        return None


def _is_token_valid(token: str) -> bool:
    """Liefert True, wenn das Token existiert und nicht abgelaufen ist.

    Unterstützt Legacy-Session-Tokens (Direkt-Lookup) und JWTs. Läuft über den
    Session-Store, der den Ablauf beachtet (abgelaufene Einträge räumen).
    """
    # Legacy: direkter Session-Lookup (Dev-Modus-Token und Rückwärtskompatibilität)
    if _session_store.get(token) is not None:
        return True

    # JWT: dekodieren und verifizieren, dann serverseitigen Widerruf prüfen
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


def _resolve_session_id(token: str) -> str | None:
    """Storage-Key der Session zu einem Token (Roh-Token bei Legacy-Sessions,
    sonst der JWT-``sid``). Für Handler, die die gespeicherte Session MUTIEREN
    müssen (Impersonation Start/Stop, P-B) — getrennt von
    ``_get_session_from_token``, das nur die Daten liefert (RedisSessionStore gibt
    eine Kopie zurück, In-place-Mutation würde dort verpuffen)."""
    if _session_store.get(token) is not None:
        return token
    payload = _decode_jwt(token)
    if payload is None:
        return None
    return payload.get("sid")


def _bearer_token(authorization: str | None) -> str | None:
    """Extrahiert das Token aus einem ``Authorization: Bearer <token>``-Header."""
    if isinstance(authorization, str) and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip() or None
    return None


def get_current_user(
    request: Request,
    x_auth_token: str | None = Header(None),
    authorization: str | None = Header(None),
) -> dict | None:
    """Liefert das Benutzer-dict zum Token, sonst None.

    Priorität: Authorization: Bearer → X-Auth-Token-Header → sp5_token-Cookie
    → ?token=-Query-Param (bleibt für SSE-Verbindungen, wo EventSource keine
    Header setzen kann). Das von ``/api/auth/login`` ausgegebene Token ist
    damit als HttpOnly-Cookie (SPA) UND als Standard-Bearer-Token
    (API-Clients) nutzbar.

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
        session = _get_session_from_token(token)
        if session is not None and session.get("_impersonation_identity") is not None:
            # P-B Admin-Impersonation („Als Benutzer ansehen"): Der echte Admin
            # behält Token/Session unverändert; hier wird NUR der Autorisierungs-
            # Principal auf die Ziel-Identität abgebildet, sodass Rolle/ID/Rechte/
            # Sichtbarkeit exakt die des Ziel-Users sind (nie mehr als der echte
            # Admin). Der Token-/Login-/Digest-Pfad bleibt komplett unberührt.
            return {
                **session["_impersonation_identity"],
                "_impersonated_by": session.get("_impersonated_by"),
                "_impersonation_active": True,
            }
        return session
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
    """Liefert eine Datenbankverbindung des konfigurierten Backends.

    Liefert SP5Database (DBF) oder SP5PostgresDatabase (PostgreSQL),
    abhängig von der Umgebungsvariable DB_BACKEND.
    """
    from sp5lib.db_config import is_postgresql

    if is_postgresql():
        from sp5lib.db_factory import get_database

        return get_database()
    else:
        import sp5api.main as _main

        return SP5Database(_main.DB_PATH)


def invalidate_sessions_for_user(user_id: int, except_session_id: str | None = None) -> int:
    """Entfernt alle aktiven Sessions einer Benutzer-ID. Liefert die Anzahl.

    Funktioniert für Legacy-Token-Schlüssel und JWT-Session-IDs. Mit
    except_session_id bleibt die passende Session erhalten (hält beim
    Self-Service-Passwortwechsel die eigene Session am Leben).
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
    """Entfernt alle abgelaufenen Sessions aus dem In-Memory-Store. Liefert die Anzahl.

    Nur fürs memory-Backend sinnvoll; beim redis-Backend räumt Redis
    abgelaufene Sessions über Key-TTLs selbst — nichts zu tun.
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
    """Hängt ein strukturiertes Audit-Ereignis an die Audit-JSON-Lines-Datei an.

    Jede Zeile ist ein eigenständiges JSON-Objekt mit Zeitstempel, Aktion, Akteur und Details.
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
    """Loggt die volle Exception mit Traceback, liefert einen bereinigten Fehler.

    Dateisystem-/Rechte-Fehler bekommen eine klare, spezifische Meldung (siehe
    ``describe_write_error``); alles andere bleibt eine generische 500.
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
