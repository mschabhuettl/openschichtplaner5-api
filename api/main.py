"""FastAPI application for OpenSchichtplaner5."""

import os
import sys
import threading
import time as _startup_time_module
from collections import deque
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Tell the libopenschichtplaner5 (sp5lib) package where this app's backend root is,
# so it can locate backend/data, backend/api/data and the Alembic dir even when
# installed standalone in site-packages. Computed relative to this file
# (api/main.py → api/ → backend/). setdefault so an explicit env override wins.
os.environ.setdefault(
    "SP5_BACKEND_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_APP_START_TIME = _startup_time_module.time()


# ── In-memory metrics collector ──────────────────────────────────
class _Metrics:
    """Simple thread-safe in-memory metrics for observability."""

    def __init__(self, latency_window: int = 100):
        self._lock = threading.Lock()
        self.request_count = 0
        self.error_count = 0  # 5xx responses
        self.not_found_count = 0  # 404 responses
        self.cache_hit_count = 0  # responses with Cache-Control max-age (hits)
        self.cache_total_count = 0  # cacheable requests total
        # Circular buffer of recent DB-read latencies (ms)
        self._latencies: deque = deque(maxlen=latency_window)

    def record_request(
        self, status: int, duration_ms: float, path: str, response_headers: dict
    ):
        with self._lock:
            self.request_count += 1
            if status >= 500:
                self.error_count += 1
            elif status == 404:
                self.not_found_count += 1
            # Cache tracking: count cacheable API paths
            _CACHEABLE_PREFIXES = (
                "/api/shifts",
                "/api/holidays",
                "/api/leave-types",
                "/api/workplaces",
                "/api/groups",
                "/api/extracharges",
            )
            if any(path.startswith(p) for p in _CACHEABLE_PREFIXES):
                self.cache_total_count += 1
                cc = response_headers.get("cache-control", "")
                if "max-age" in cc and status == 200:
                    self.cache_hit_count += 1

    def record_db_latency(self, ms: float):
        with self._lock:
            self._latencies.append(ms)

    def snapshot(self) -> dict:
        with self._lock:
            total = self.request_count or 1
            err_rate = round(self.error_count / total, 4)
            cache_total = self.cache_total_count or 1
            cache_hit_rate = round(self.cache_hit_count / cache_total, 4)
            latencies = list(self._latencies)
        avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else None
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "not_found_count": self.not_found_count,
            "error_rate": err_rate,
            "cache_hit_count": self.cache_hit_count,
            "cache_total_count": self.cache_total_count,
            "cache_hit_rate": cache_hit_rate,
            "db_read_latency_avg_ms": avg_lat,
            "db_read_latency_samples": len(latencies),
        }


_metrics = _Metrics()

# Load .env file if present
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from datetime import UTC

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402

# ── Import shared dependencies ──────────────────────────────────
# These are re-exported here so tests can still do `from api.main import _sessions`
from .dependencies import (  # noqa: E402
    _DEV_MODE_ACTIVE,
    _DEV_TOKEN,
    _DEV_USER,
    _is_token_valid,
    _logger,
    _sessions,
    get_db,
    limiter,
    purge_expired_sessions,
    purge_stale_failed_logins,
)

# ── Dev-mode session ────────────────────────────────────────────
# Only active when SP5_DEV_MODE=true (never in production!)
if os.environ.get("SP5_DEV_MODE", "").lower() in ("1", "true", "yes"):
    _sessions[_DEV_TOKEN] = {**_DEV_USER, "expires_at": None}
    _logger.warning(
        "DEV MODE ACTIVE — dev token enabled (SP5_DEV_MODE=true). Do not use in production!"
    )

# ── Config ──────────────────────────────────────────────────────
DB_PATH = os.environ.get(
    "SP5_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "sp5_db", "Daten"),
)
DB_PATH = os.path.normpath(DB_PATH)

# CORS origins from env
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()] or [
    "http://localhost:5173",
    "http://localhost:8000",
]

_OPENAPI_TAGS = [
    {"name": "Health", "description": "System health and version info"},
    {"name": "Auth", "description": "Authentication: login and logout"},
    {"name": "Companies", "description": "Company / tenant management (multi-tenant)"},
    {"name": "Employees", "description": "Employee management (CRUD)"},
    {"name": "Groups", "description": "Group management and member assignments"},
    {"name": "Shifts", "description": "Shift definitions (CRUD)"},
    {"name": "Schedule", "description": "Schedule read and write operations"},
    {"name": "Absences", "description": "Absence/leave entries"},
    {"name": "Statistics", "description": "Monthly and yearly statistics"},
    {"name": "Users", "description": "API user management (Admin only)"},
    {"name": "Export", "description": "CSV/HTML/PDF export endpoints"},
    {"name": "Import", "description": "CSV import endpoints"},
    {"name": "Backup", "description": "Database backup and restore"},
    {"name": "Notes", "description": "Shift notes and handover entries"},
    {
        "name": "Self-Service",
        "description": "Employee self-service: own wishes, absences, profile",
    },
    {"name": "Events", "description": "Calendar events and holidays"},
    {"name": "Admin", "description": "Administrative operations (Admin only)"},
    {"name": "iCal", "description": "iCal (.ics) export for calendar integration"},
    {"name": "Webhooks", "description": "Webhook management and delivery (Admin only)"},
]


async def _periodic_cleanup():
    """Background task: purge expired sessions and stale failed-login entries every 5 minutes."""
    import asyncio

    while True:
        await asyncio.sleep(300)
        try:
            sess = purge_expired_sessions()
            logins = purge_stale_failed_logins()
            if sess or logins:
                _logger.debug(
                    "Periodic cleanup: removed %d expired sessions, %d stale lockout entries",
                    sess,
                    logins,
                )
        except Exception as _exc:  # pragma: no cover
            _logger.warning("Periodic cleanup error: %s", _exc)


def _check_db_files_on_startup(db_path: str) -> None:
    """Check that critical DBF files are readable on startup. Logs warnings for missing/unreadable files."""
    CRITICAL_TABLES = ["EMPL", "USER", "SHIFT", "MASHI", "ABSEN"]
    missing = []
    for table in CRITICAL_TABLES:
        path = os.path.join(db_path, f"5{table}.DBF")
        if not os.path.exists(path):
            missing.append(f"5{table}.DBF (not found)")
        elif not os.access(path, os.R_OK):
            missing.append(f"5{table}.DBF (not readable)")
    if missing:
        _logger.error(
            "STARTUP DB CHECK FAILED — %d critical table(s) missing/unreadable: %s",
            len(missing),
            ", ".join(missing),
        )
    else:
        _logger.info(
            "Startup DB check OK — all %d critical tables accessible at %s",
            len(CRITICAL_TABLES),
            db_path,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # ── Auto-migration on startup ──────────────────────────────
    try:
        from sp5lib.auto_migrate import run_startup_migration

        migration_result = run_startup_migration()
        if migration_result.error:
            _logger.error("Startup auto-migration FAILED: %s", migration_result.error)
        elif migration_result.skipped:
            _logger.info("Startup auto-migration skipped: %s", migration_result.skip_reason)
        elif migration_result.had_migrations:
            _logger.info(
                "Startup auto-migration OK: %s → %s (%d applied)",
                migration_result.previous_version or "(empty)",
                migration_result.current_version,
                len(migration_result.migrations_applied),
            )
        else:
            _logger.info("Startup auto-migration: no migrations needed")
    except Exception as _exc:
        _logger.warning("Startup auto-migration error: %s", _exc)

    # Startup DB accessibility check
    _check_db_files_on_startup(DB_PATH)
    # Auto-backup on startup (only if last backup > 24h old)
    try:
        from .routers.admin import create_auto_backup

        created = create_auto_backup()
        if created:
            _logger.info("Startup auto-backup: %s", created)
    except Exception as _exc:
        _logger.warning("Startup auto-backup failed: %s", _exc)
    # Start background cleanup task
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    # Start scheduled reports background scheduler
    try:
        from .routers.scheduled_reports import start_scheduler, stop_scheduler
        start_scheduler(interval_seconds=300)
        _logger.info("Scheduled reports scheduler started")
    except Exception as _exc:
        _logger.warning("Scheduled reports scheduler start failed: %s", _exc)
    yield
    cleanup_task.cancel()
    try:
        stop_scheduler()
    except Exception:
        pass
    _logger.info("SP5 API shutting down — cleaning up resources")


app = FastAPI(
    lifespan=lifespan,
    title="OpenSchichtplaner5 API",
    description=(
        "Open-source REST API for Schichtplaner5 databases.\n\n"
        "## API Versioning\n"
        "All endpoints are available under `/api/v1/` (recommended) and `/api/` (deprecated).\n"
        "Unversioned `/api/` routes return `Deprecation: true` and `Sunset` headers.\n"
        "New clients should use `/api/v1/` exclusively.\n\n"
        "## Authentication\n"
        "Most endpoints require an `x-auth-token` header obtained from `POST /api/v1/auth/login`.\n\n"
        "## Roles\n"
        "- **Leser** – read-only access\n"
        "- **Planer** – can write schedules and absences\n"
        "- **Admin** – full access including user and master-data management\n"
    ),
    version="1.1.0",
    openapi_tags=_OPENAPI_TAGS,
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Custom 429 handler — returns structured JSON with Retry-After header."""
    import re as _re_mod

    from api.rate_limit_store import log_rate_limit_event

    client_ip = request.client.host if request.client else "unknown"
    endpoint = request.url.path

    _logger.warning(
        "RATE_LIMIT 429 | ip=%s path=%s detail=%s",
        client_ip,
        endpoint,
        exc.detail,
    )

    # Resolve authenticated user (if any)
    _rl_user: str | None = None
    _rl_token = (
        request.headers.get("x-auth-token")
        or request.cookies.get("sp5_token")
        or request.query_params.get("token")
    )
    if _rl_token:
        from .dependencies import _get_session_from_token

        _rl_session = _get_session_from_token(_rl_token)
        if _rl_session:
            _rl_user = _rl_session.get("NAME")

    # Persist rate-limit event for the admin dashboard
    log_rate_limit_event(
        user=_rl_user,
        ip=client_ip,
        endpoint=endpoint,
        detail=str(exc.detail) if exc.detail else "",
    )
    # Parse retry_after seconds from slowapi's detail string (e.g. "5 per 1 minute")
    retry_after = 60  # sensible default
    detail_str = str(exc.detail) if exc.detail else ""
    m = _re_mod.search(
        r"(\d+)\s+per\s+(\d+)\s*(second|minute|hour|day)",
        detail_str,
        _re_mod.IGNORECASE,
    )
    if m:
        per_value = int(m.group(2))
        unit = m.group(3).lower()
        unit_seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
        retry_after = per_value * unit_seconds.get(unit, 60)

    response = JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "retry_after": retry_after,
            "message": f"Zu viele Anfragen. Bitte {retry_after} Sekunden warten.",
            "detail": f"Zu viele Anfragen. Bitte warte kurz und versuche es erneut. ({detail_str})",
        },
    )
    response.headers["Retry-After"] = str(retry_after)
    response = request.app.state.limiter._inject_headers(
        response, request.state.view_rate_limit
    )
    return response


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "x-auth-token", "Authorization"],
)


@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    """Set Cache-Control headers for static/rarely-changing GET endpoints."""
    response = await call_next(request)
    if request.method == "GET":
        path = request.url.path
        # Rarely-changing master data: cache for 60s client-side
        _CACHEABLE_PREFIXES = (
            "/api/shifts",
            "/api/holidays",
            "/api/leave-types",
            "/api/workplaces",
            "/api/groups",
            "/api/extracharges",
        )
        if (
            any(path.startswith(p) for p in _CACHEABLE_PREFIXES)
            and response.status_code == 200
        ):
            response.headers["Cache-Control"] = "private, max-age=60"
        elif path.startswith("/api/"):
            # All other API responses: no caching
            response.headers.setdefault(
                "Cache-Control", "no-cache, no-store, must-revalidate"
            )
    return response


_CSP_REPORT_ONLY = os.environ.get("CSP_REPORT_ONLY", "").lower() in (
    "1",
    "true",
    "yes",
)


def _build_csp() -> str:
    """Build the Content-Security-Policy header value."""
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


_CSP_VALUE = _build_csp()


def _apply_security_headers(response):
    """Apply security headers to a response object.

    Extracted as a helper so that early-return responses (e.g. 401 from
    auth_middleware) also get security headers, not only responses that
    pass through the full middleware stack.
    """
    response.headers["X-API-Version"] = "1"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Content Security Policy: restrict resource loading to same origin
    # Use Report-Only mode when CSP_REPORT_ONLY=true (for debugging)
    csp_header = (
        "Content-Security-Policy-Report-Only"
        if _CSP_REPORT_ONLY
        else "Content-Security-Policy"
    )
    response.headers[csp_header] = _CSP_VALUE
    # Additional security headers
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    # Only send HSTS if running in production (check env)
    if os.environ.get("SP5_HSTS", "").lower() in ("1", "true", "yes"):
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    _apply_security_headers(response)
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Translate Pydantic validation errors into German user-friendly messages."""
    _TYPE_MSGS = {
        "missing": "Required field missing",
        "int_parsing": "Must be an integer",
        "float_parsing": "Must be a number",
        "bool_parsing": "Must be true or false",
        "string_too_short": "Input too short",
        "string_too_long": "Input too long",
        "string_pattern_mismatch": "Invalid format",
        "greater_than": "Must be greater than {gt}",
        "greater_than_equal": "Must be at least {ge}",
        "less_than_equal": "Must be at most {le}",
        "less_than": "Must be less than {lt}",
        "type_error": "Wrong data type",
    }
    # Types where we prefer the custom validator message over the generic mapping
    _PASS_THROUGH_TYPES = {"value_error", "assertion_error"}
    errors = []
    for e in exc.errors():
        field = ".".join(
            str(loc) for loc in e.get("loc", []) if loc not in ("body", "query", "path")
        )
        etype = e.get("type", "")
        raw_msg = e.get("msg", "Invalid value")
        # Strip pydantic's "Value error, " prefix from custom validators
        if raw_msg.startswith("Value error, "):
            raw_msg = raw_msg[len("Value error, "):]
        if etype in _PASS_THROUGH_TYPES:
            # Use the custom message from the validator directly
            msg = raw_msg
        elif etype in _TYPE_MSGS:
            template = _TYPE_MSGS[etype]
            ctx = e.get("ctx", {})
            try:
                msg = template.format(**ctx)
            except (KeyError, IndexError):
                msg = template
        else:
            msg = raw_msg
        if field:
            errors.append(f"{field}: {msg}")
        else:
            errors.append(msg)
    detail = "; ".join(errors) if errors else "Invalid input"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions, log with details, return sanitized 500."""
    from .dependencies import request_id_ctx

    token = (
        request.headers.get("x-auth-token")
        or request.cookies.get("sp5_token")
        or request.query_params.get("token")
    )
    user = _sessions.get(token, {}).get("NAME", "-") if token else "-"
    rid = request_id_ctx.get(None) or "-"
    _logger.error(
        "Unhandled exception: %s %s | user=%s | %s: %s",
        request.method,
        request.url.path,
        user,
        type(exc).__name__,
        str(exc),
        exc_info=True,
        extra={
            "request_id": rid,
            "exc_type": type(exc).__name__,
            "event": "unhandled_exception",
        },
    )

    return JSONResponse(
        status_code=500,
        content={"detail": "Interner Serverfehler. Bitte versuche es erneut."},
    )


# ── Public paths (no auth required) ────────────────────────────
_PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api",
    "/api/health",
    "/api/metrics",
    "/api/version",
    "/",
    "/api/errors",
    "/api/dev/mode",
}


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request with timing info and request-ID via structured logging."""
    import time as _t
    import uuid as _uuid

    from .dependencies import request_id_ctx

    # Use incoming X-Request-ID or generate a new UUID
    req_id = request.headers.get("x-request-id") or str(_uuid.uuid4())
    # Set request_id in contextvars so all log entries include it
    token_cv = request_id_ctx.set(req_id)
    start = _t.time()
    try:
        response = await call_next(request)
    except Exception:
        request_id_ctx.reset(token_cv)
        raise
    duration_ms = round((_t.time() - start) * 1000)
    token = (
        request.headers.get("x-auth-token")
        or request.cookies.get("sp5_token")
        or request.query_params.get("token")
    )
    user = _sessions.get(token, {}).get("NAME", "-") if token else "-"
    _logger.info(
        "%s %s %d %dms user=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        user,
        extra={
            "request_id": req_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "username": user,
        },
    )
    response.headers["X-Request-ID"] = req_id
    request_id_ctx.reset(token_cv)
    # Record metrics (after response so headers are set)
    _metrics.record_request(
        status=response.status_code,
        duration_ms=duration_ms,
        path=request.url.path,
        response_headers=dict(response.headers),
    )
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require authentication for all /api/* endpoints except public ones."""
    path = request.url.path
    method = request.method
    client_ip = request.client.host if request.client else "unknown"

    if path in _PUBLIC_PATHS or not path.startswith("/api/"):
        return await call_next(request)
    # iCal feed uses token-in-URL authentication (for calendar app subscriptions)
    if path.startswith("/api/ical/feed/"):
        return await call_next(request)
    # SSE endpoint also accepts token as query param (EventSource doesn't support headers)
    # Priority: X-Auth-Token header → sp5_token cookie → ?token= query param
    token = (
        request.headers.get("x-auth-token")
        or request.cookies.get("sp5_token")
        or request.query_params.get("token")
    )
    if not token or not _is_token_valid(token):
        _logger.warning("AUTH 401 | ip=%s method=%s path=%s", client_ip, method, path)
        resp_401 = JSONResponse(status_code=401, content={"detail": "Nicht angemeldet"})
        _apply_security_headers(resp_401)
        return resp_401
    response = await call_next(request)
    if response.status_code == 403:
        user_info = _sessions.get(token, {})
        _logger.warning(
            "AUTH 403 | ip=%s method=%s path=%s user=%s",
            client_ip,
            method,
            path,
            user_info.get("NAME", "?"),
        )
    if method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        user_info = _sessions.get(token, {})
        _logger.info(
            "WRITE %s | ip=%s path=%s user=%s",
            method,
            client_ip,
            path,
            user_info.get("NAME", "?"),
        )
    return response


# ── API Versioning Middleware ────────────────────────────────────
# Rewrites /api/v1/... → /api/... so existing routes handle both prefixes.
# Adds Deprecation + Sunset headers on unversioned /api/ requests.


@app.middleware("http")
async def api_versioning_middleware(request: Request, call_next):
    """Handle /api/v1/ prefix and add deprecation headers on unversioned /api/ routes."""
    from datetime import datetime as _dt
    from datetime import timedelta

    path = request.url.path
    is_versioned = False

    # Don't rewrite OpenAPI docs paths — they are served directly by FastAPI
    _DOCS_PATHS = {"/api/v1/docs", "/api/v1/redoc", "/api/v1/openapi.json"}
    if (path.startswith("/api/v1/") or path == "/api/v1") and path not in _DOCS_PATHS:
        # Rewrite /api/v1/... → /api/...
        new_path = "/api" + path[7:]  # strip "/api/v1" prefix, keep rest
        request.scope["path"] = new_path
        is_versioned = True

    response = await call_next(request)

    # Add deprecation headers on unversioned /api/ routes (not /api/v1/)
    if not is_versioned and path.startswith("/api/"):
        response.headers["Deprecation"] = "true"
        sunset_date = (_dt.now(UTC) + timedelta(days=365)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        response.headers["Sunset"] = sunset_date
        response.headers["Link"] = f'</api/v1{path[4:]}>; rel="successor-version"'

    return response


# ── Changelog Middleware ────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.requests import Request as StarletteRequest  # noqa: E402


class ChangelogMiddleware(BaseHTTPMiddleware):
    """Automatically log CREATE/UPDATE/DELETE actions from the API."""

    _ENTITY_MAP = {
        "employees": "employee",
        "groups": "group",
        "shifts": "shift",
        "leave-types": "leave_type",
        "holidays": "holiday",
        "workplaces": "workplace",
        "schedule": "schedule",
        "absences": "absence",
        "users": "user",
        "extracharges": "extracharge",
    }

    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        method = request.method
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            return response
        if response.status_code >= 300:
            return response
        path = request.url.path
        if "changelog" in path or "backup" in path or "compact" in path:
            return response
        parts = [p for p in path.strip("/").split("/") if p]
        entity = "unknown"
        entity_id = 0
        if len(parts) >= 2:
            segment = parts[1]
            entity = self._ENTITY_MAP.get(segment, segment.replace("-", "_"))
        if len(parts) >= 3:
            try:
                entity_id = int(parts[2])
            except ValueError:
                entity_id = 0
        action_map = {
            "POST": "CREATE",
            "PUT": "UPDATE",
            "PATCH": "UPDATE",
            "DELETE": "DELETE",
        }
        action = action_map.get(method, method)
        # Try to resolve actual user from session token
        actor_name = "api"
        actor_id = None
        token = (
            request.headers.get("x-auth-token")
            or request.cookies.get("sp5_token")
            or request.query_params.get("token")
        )
        if token:
            session = _sessions.get(token)
            if session:
                actor_name = session.get("NAME", "api")
                actor_id = session.get("ID")
        try:
            get_db().log_action(
                user=actor_name,
                action=action,
                entity=entity,
                entity_id=entity_id,
                details=f"{method} {path}",
                user_id=actor_id,
            )
        except Exception:
            _logger.debug("Changelog middleware audit log failed", exc_info=True)
        return response


app.add_middleware(ChangelogMiddleware)


# RequestLoggingMiddleware removed — duplicate of request_logging_middleware above

# ── Include routers ─────────────────────────────────────────────
from .routers import (  # noqa: E402
    absences,
    admin,
    auth,
    availability,
    companies,
    conflict_report,
    email,
    employees,
    events,
    export_scheduler,
    ical,
    master_data,
    misc,
    notification_settings,
    notifications,
    overtime,
    qualification_matrix,
    recurring_shifts,
    reports,
    schedule,
    schedule_comments,
    schedule_pdf,
    scheduled_reports,
    webhooks,
    work_time_rules,
)

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(qualification_matrix.router)  # must be before employees (path conflict: /api/employees/{emp_id})
app.include_router(employees.router)
app.include_router(schedule_comments.router)  # must be before schedule.router (path conflict)
app.include_router(schedule_pdf.router)  # must be before schedule.router (path conflict: /api/schedule/pdf)
app.include_router(schedule.router)
app.include_router(absences.router)
app.include_router(master_data.router)
app.include_router(reports.router)
app.include_router(conflict_report.router)
app.include_router(admin.router)
app.include_router(misc.router)
app.include_router(events.router)
app.include_router(notifications.router)
app.include_router(ical.router)
app.include_router(email.router)
app.include_router(availability.router)
app.include_router(recurring_shifts.router)
app.include_router(webhooks.router)
app.include_router(overtime.router)
app.include_router(export_scheduler.router)
app.include_router(scheduled_reports.router)
app.include_router(work_time_rules.router)
app.include_router(notification_settings.router)


# ── Routes ──────────────────────────────────────────────────────

_API_VERSION = "1.0.0"


def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _get_dir_size(path: str) -> int:
    """Get total size of files in a directory (non-recursive)."""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
    except OSError:
        return -1
    return total


@app.get(
    "/api/health",
    tags=["Health"],
    summary="Health check",
    description=(
        "Returns extended service health: DB status, disk, memory, uptime, "
        "version, and aggregated check results. "
        "This endpoint is public (no authentication required)."
    ),
)
def health():
    """Extended health check endpoint — public, no auth required.

    Returns aggregated health status with checks for DB, disk, memory,
    plus uptime, version, and session count.
    Sensitive info (paths, errors) is never exposed.
    """
    import shutil
    import time as _t

    import psutil

    now = _t.time()

    # ── DB check ──
    db_check = "ok"
    try:
        db = get_db()
        db.get_stats()
    except Exception:
        db_check = "error"

    CRITICAL_TABLES = ["EMPL", "USER", "SHIFT", "MASHI", "ABSEN"]
    dbf_ok_count = 0
    dbf_missing: list[str] = []
    latest_mtime: float = 0.0
    for table in CRITICAL_TABLES:
        fpath = os.path.join(DB_PATH, f"5{table}.DBF")
        if os.path.exists(fpath) and os.access(fpath, os.R_OK):
            dbf_ok_count += 1
            try:
                mtime = os.path.getmtime(fpath)
                if mtime > latest_mtime:
                    latest_mtime = mtime
            except OSError:
                pass
        else:
            dbf_missing.append(f"5{table}.DBF")

    if dbf_missing:
        db_check = "error"

    from datetime import datetime

    db_details: dict = {
        "status": db_check,
        "dbf_ok": dbf_ok_count,
        "dbf_missing": dbf_missing,
    }
    if latest_mtime > 0:
        db_details["last_modified"] = datetime.fromtimestamp(
            latest_mtime, tz=UTC
        ).isoformat()

    # ── Disk check ──
    disk_check = "ok"
    disk_details: dict = {}
    try:
        usage = shutil.disk_usage(DB_PATH)
        free_mb = usage.free / (1024 * 1024)
        disk_details["free_mb"] = round(free_mb, 1)
        disk_details["total_mb"] = round(usage.total / (1024 * 1024), 1)
        disk_details["used_percent"] = round(
            (usage.used / usage.total) * 100, 1
        )
        db_dir_size = _get_dir_size(DB_PATH)
        if db_dir_size >= 0:
            disk_details["db_dir_size_mb"] = round(
                db_dir_size / (1024 * 1024), 2
            )
        if free_mb < 100:
            disk_check = "warning"
        if free_mb < 20:
            disk_check = "error"
    except OSError:
        disk_check = "error"

    # ── Memory check ──
    memory_check = "ok"
    memory_details: dict = {}
    try:
        process = psutil.Process()
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / (1024 * 1024)
        memory_details["rss_mb"] = round(rss_mb, 1)
        vm = psutil.virtual_memory()
        memory_details["system_used_percent"] = round(vm.percent, 1)
        memory_details["system_available_mb"] = round(
            vm.available / (1024 * 1024), 1
        )
        if rss_mb > 512:
            memory_check = "warning"
        if rss_mb > 1024:
            memory_check = "error"
    except Exception:
        memory_check = "error"

    # ── Uptime ──
    uptime_seconds = round(now - _APP_START_TIME, 1)
    uptime_human = _format_uptime(now - _APP_START_TIME)
    started_at = datetime.fromtimestamp(_APP_START_TIME, tz=UTC).isoformat()

    # ── Active sessions ──
    active_sessions = sum(
        1
        for s in _sessions.values()
        if s.get("expires_at") is None or s.get("expires_at", 0) > now
    )

    # ── Aggregate status ──
    checks = {
        "db": db_check,
        "disk": disk_check,
        "memory": memory_check,
    }
    check_values = list(checks.values())
    if "error" in check_values:
        overall = "unhealthy"
    elif "warning" in check_values:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "checks": checks,
        "version": _API_VERSION,
        "uptime": uptime_human,
        "uptime_seconds": uptime_seconds,
        "started_at": started_at,
        "db": db_details,
        "disk": disk_details,
        "memory": memory_details,
        "sessions": {"active": active_sessions},
    }


@app.get(
    "/api/metrics",
    tags=["Health"],
    summary="Runtime metrics",
    description=(
        "Returns in-process metrics: request count, error rate, cache hit rate, "
        "and average DB-read latency (last 100 requests). "
        "No authentication required when called from localhost."
    ),
)
def get_metrics(request: Request):
    """Runtime metrics endpoint — no auth required for localhost."""
    client_host = request.client.host if request.client else ""
    is_local = client_host in ("127.0.0.1", "::1", "localhost")
    snap = _metrics.snapshot()
    import time as _t

    snap["uptime_seconds"] = round(_t.time() - _APP_START_TIME, 1)
    snap["active_sessions"] = sum(
        1
        for s in _sessions.values()
        if s.get("expires_at") is None or s.get("expires_at", 0) > _t.time()
    )
    snap["local_request"] = is_local
    return snap


@app.get(
    "/api/version",
    tags=["Health"],
    summary="API version",
    description=(
        "Returns the current API version, build date, Python version, and service name. "
        "No authentication required."
    ),
)
def version():
    """Return current API version — public, no auth required."""
    import platform
    from datetime import datetime as _dt

    return {
        "version": _API_VERSION,
        "service": "OpenSchichtplaner5 API",
        "python_version": platform.python_version(),
        "build_date": _dt.now(UTC).strftime("%Y-%m-%d"),
        "min_compatible_frontend": "0.4.0",
    }


@app.get(
    "/api",
    tags=["Health"],
    summary="API root",
    description="Returns basic service info.",
)
def root():
    """Return basic service info for the API root endpoint."""
    return {
        "service": "OpenSchichtplaner5 API",
        "version": _API_VERSION,
        "backend": "dbf",
    }


@app.get("/", include_in_schema=False)
async def frontend_root():
    """Serve the React frontend."""
    _dist = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    )
    index = os.path.join(_dist, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"service": "OpenSchichtplaner5 API", "version": _API_VERSION}


@app.get("/api/dev/mode", tags=["Health"], summary="Dev mode status")
def get_dev_mode():
    """Returns whether SP5_DEV_MODE is active. Safe to call without auth."""
    return {"dev_mode": _DEV_MODE_ACTIVE}


@app.get(
    "/api/migration/status",
    tags=["Admin"],
    summary="Database migration status",
    description=(
        "Returns the current database schema version, target version, "
        "and whether auto-migration is enabled."
    ),
)
def get_migration_status():
    """Return current migration/schema version status."""
    from sp5lib.auto_migrate import (
        DBF_SCHEMA_VERSION,
        _get_alembic_head,
        _get_db_revision,
        _get_dbf_schema_version,
        _is_auto_migrate_enabled,
    )
    from sp5lib.db_config import BACKEND_POSTGRESQL, get_database_url, get_db_backend

    backend = get_db_backend()
    auto_migrate = _is_auto_migrate_enabled()

    if backend == BACKEND_POSTGRESQL:
        database_url = get_database_url()
        db_rev = _get_db_revision(database_url) if database_url else None
        head_rev = _get_alembic_head()
        return {
            "backend": backend,
            "auto_migrate_enabled": auto_migrate,
            "db_revision": db_rev,
            "target_revision": head_rev,
            "up_to_date": db_rev == head_rev and db_rev is not None,
        }
    else:
        db_path = os.environ.get("SP5_DB_PATH", DB_PATH)
        current = _get_dbf_schema_version(db_path)
        return {
            "backend": backend,
            "auto_migrate_enabled": auto_migrate,
            "db_schema_version": current,
            "target_schema_version": DBF_SCHEMA_VERSION,
            "up_to_date": current == DBF_SCHEMA_VERSION,
        }


@app.get("/api/stats", tags=["Health"], summary="Database statistics")
def get_stats():
    """Return database statistics from the connected SP5 database."""
    return get_db().get_stats()


# ── Dashboard Summary ────────────────────────────────────────


@app.get("/api/dashboard/summary", tags=["Health"], summary="Dashboard summary")
def get_dashboard_summary(
    year: int | None = Query(
        None, description="Year (YYYY), defaults to current year"
    ),
    month: int | None = Query(
        None, description="Month (1-12), defaults to current month"
    ),
):
    """Return all KPIs needed for the Dashboard in one request."""
    import calendar as _cal
    from collections import defaultdict
    from datetime import date, timedelta
    from datetime import datetime as _dt

    from sp5lib.color_utils import bgr_to_hex

    _today = date.today()
    if year is None:
        year = _today.year
    if month is None:
        month = _today.month

    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=400, detail="Invalid month: must be between 1 and 12"
        )

    db = get_db()
    today = date.today()
    today_str = today.isoformat()
    prefix = f"{year:04d}-{month:02d}"

    # ── Month label ───────────────────────────────────────────
    month_names_de = [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ]
    month_label = f"{month_names_de[month - 1]} {year}"

    # ── Employees ─────────────────────────────────────────────
    employees = db.get_employees(include_hidden=False)
    total_employees = len(employees)

    # ── Groups ───────────────────────────────────────────────
    groups = db.get_groups()

    # ── Shifts today ─────────────────────────────────────────
    today_entries = db.get_schedule_day(today_str)
    shifts_today_count = sum(
        1 for e in today_entries if e["kind"] in ("shift", "special_shift")
    )
    # Group by shift short name
    shift_groups: dict = defaultdict(lambda: {"count": 0, "color": "#6B7280"})
    for e in today_entries:
        if e["kind"] in ("shift", "special_shift"):
            key = e.get("display_name") or e.get("shift_short") or "?"
            shift_groups[key]["count"] += 1
            shift_groups[key]["color"] = e.get("color_bk", "#6B7280")

    by_shift = [
        {"name": k, "count": v["count"], "color": v["color"]}
        for k, v in shift_groups.items()
    ]
    by_shift.sort(key=lambda x: -x["count"])

    # ── Shifts + absences this month ─────────────────────────
    mashi_count = sum(
        1 for r in db._read("MASHI") if r.get("DATE", "").startswith(prefix)
    )
    spshi_count = sum(
        1 for r in db._read("SPSHI") if r.get("DATE", "").startswith(prefix)
    )
    total_shifts_scheduled = mashi_count + spshi_count

    # Count working days for coverage %
    num_days = _cal.monthrange(year, month)[1]
    working_days = sum(
        1 for d in range(1, num_days + 1) if _dt(year, month, d).weekday() < 5
    )
    max_possible = total_employees * working_days if working_days > 0 else 1
    coverage_pct = (
        round((total_shifts_scheduled / max_possible) * 100) if max_possible > 0 else 0
    )

    # ── Absences this month ───────────────────────────────────
    lt_map = {lt["ID"]: lt for lt in db.get_leave_types(include_hidden=True)}
    abs_by_type: dict = defaultdict(
        lambda: {"count": 0, "name": "", "color": "#6B7280"}
    )
    total_absences_month = 0

    for r in db._read("ABSEN"):
        if r.get("DATE", "").startswith(prefix):
            total_absences_month += 1
            ltid = r.get("LEAVETYPID")
            lt = lt_map.get(ltid) if ltid else None
            key = lt.get("SHORTNAME") or lt.get("NAME", "?") if lt else "?"
            abs_by_type[key]["count"] += 1
            if lt:
                abs_by_type[key]["name"] = lt.get("NAME", key)
                abs_by_type[key]["color"] = bgr_to_hex(lt.get("COLORBK", 16777215))
            else:
                abs_by_type[key]["name"] = key

    absences_by_type_list = [
        {"short": k, "name": v["name"], "count": v["count"], "color": v["color"]}
        for k, v in abs_by_type.items()
    ]
    absences_by_type_list.sort(key=lambda x: -x["count"])

    # ── Zeitkonto alerts (employees with > 8h deficit this month) ─────────────
    try:
        stats = db.get_statistics(year, month)
        zeitkonto_alerts = []
        for s in stats:
            if s["overtime_hours"] < -8:
                zeitkonto_alerts.append(
                    {
                        "employee": s["employee_name"],
                        "employee_short": s["employee_short"],
                        "hours_diff": round(s["overtime_hours"], 1),
                    }
                )
        zeitkonto_alerts.sort(key=lambda x: x["hours_diff"])
        zeitkonto_alerts = zeitkonto_alerts[:10]
    except Exception:
        zeitkonto_alerts = []

    # ── Upcoming birthdays (next 30 days) ─────────────────────
    upcoming_birthdays = []
    for emp in employees:
        bday_raw = emp.get("BIRTHDAY")
        if not bday_raw or len(bday_raw) < 10:
            continue
        try:
            bday_month = int(bday_raw[5:7])
            bday_day = int(bday_raw[8:10])
            bday_this_year = date(today.year, bday_month, bday_day)
            if bday_this_year < today:
                bday_this_year = date(today.year + 1, bday_month, bday_day)
            days_until = (bday_this_year - today).days
            if 0 <= days_until <= 30:
                name = f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", ")
                upcoming_birthdays.append(
                    {
                        "name": name,
                        "date": bday_raw[5:],  # MM-DD
                        "days_until": days_until,
                    }
                )
        except (ValueError, IndexError):
            continue
    upcoming_birthdays.sort(key=lambda x: x["days_until"])

    # ── Staffing warnings (next 7 days vs SHDEM) ──────────────
    staffing_warnings = []
    try:
        staffing_req = db.get_staffing_requirements()
        shift_reqs = staffing_req.get("shift_requirements", [])

        if shift_reqs:
            for day_offset in range(7):
                check_date = today + timedelta(days=day_offset)
                check_str = check_date.isoformat()
                weekday = check_date.weekday()  # 0=Mon..6=Sun

                day_ents = db.get_schedule_day(check_str)
                actual_by_shift: dict = defaultdict(int)
                for e in day_ents:
                    if e["kind"] in ("shift", "special_shift") and e.get("shift_id"):
                        actual_by_shift[e["shift_id"]] += 1

                for req in shift_reqs:
                    if req.get("weekday") != weekday:
                        continue
                    min_req = req.get("min", 0) or 0
                    if min_req == 0:
                        continue
                    shift_id = req.get("shift_id")
                    actual = actual_by_shift.get(shift_id, 0)
                    if actual < min_req:
                        staffing_warnings.append(
                            {
                                "date": check_str,
                                "shift": req.get("shift_short")
                                or req.get("shift_name", "?"),
                                "shift_name": req.get("shift_name", "?"),
                                "actual": actual,
                                "required": min_req,
                                "color": req.get("color_bk", "#EF4444"),
                            }
                        )
        staffing_warnings.sort(key=lambda x: x["date"])
    except Exception:
        _logger.debug("Staffing warnings computation failed", exc_info=True)

    return {
        "employees": {"total": total_employees, "active": total_employees},
        "shifts_today": {"count": shifts_today_count, "by_shift": by_shift},
        "shifts_this_month": {
            "scheduled": total_shifts_scheduled,
            "absent": total_absences_month,
            "coverage_pct": coverage_pct,
        },
        "absences_this_month": {
            "total": total_absences_month,
            "by_type": absences_by_type_list,
        },
        "zeitkonto_alerts": zeitkonto_alerts,
        "upcoming_birthdays": upcoming_birthdays,
        "staffing_warnings": staffing_warnings,
        "groups": len(groups),
        "month_label": month_label,
    }


# ── Dashboard: Today ──────────────────────────────────────────


@app.get("/api/dashboard/today", tags=["Health"], summary="Today's schedule overview")
def get_dashboard_today():
    """Return employees on duty today, today's absences, and week peak data."""
    from datetime import date, timedelta

    db = get_db()
    today = date.today()
    today_str = today.isoformat()
    today_weekday = today.weekday()  # 0=Mon

    # Build shift map for startend lookup
    shifts_map = {s["ID"]: s for s in db.get_shifts(include_hidden=True)}

    # Helper: get startend for a shift on a given weekday
    def get_shift_startend(shift_id: int, weekday: int) -> str:
        shift = shifts_map.get(shift_id)
        if not shift:
            return ""
        key = f"STARTEND{weekday}"
        return shift.get(key, shift.get("STARTEND0", ""))

    entries = db.get_schedule_day(today_str)
    on_duty = []
    absences = []

    for e in entries:
        kind = e.get("kind")
        if kind in ("shift", "special_shift"):
            # Prefer SPSHI startend if available, else look up from SHIFT table
            startend = e.get("spshi_startend", "")
            if not startend and e.get("shift_id"):
                startend = get_shift_startend(e["shift_id"], today_weekday)
            on_duty.append(
                {
                    "employee_id": e["employee_id"],
                    "employee_name": e["employee_name"],
                    "employee_short": e["employee_short"],
                    "shift_name": e["shift_name"] or e.get("display_name", ""),
                    "shift_short": e["shift_short"] or e.get("display_name", ""),
                    "color_bk": e["color_bk"],
                    "color_text": e["color_text"],
                    "workplace_name": e.get("workplace_name", ""),
                    "startend": startend,
                }
            )
        elif kind == "absence":
            absences.append(
                {
                    "employee_id": e["employee_id"],
                    "employee_name": e["employee_name"],
                    "employee_short": e["employee_short"],
                    "leave_name": e["leave_name"],
                    "color_bk": e["color_bk"],
                    "color_text": e["color_text"],
                }
            )

    # ── Week Peak: find busiest day this week ─────────────────
    week_start = today - timedelta(days=today_weekday)  # Monday
    week_days_de = [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ]
    week_data = []
    peak_count = 0
    peak_day = None

    for i in range(7):
        day = week_start + timedelta(days=i)
        day_entries = db.get_schedule_day(day.isoformat()) if day != today else entries
        day_count = sum(
            1 for e in day_entries if e.get("kind") in ("shift", "special_shift")
        )
        week_data.append(
            {
                "date": day.isoformat(),
                "weekday_name": week_days_de[i],
                "weekday_short": week_days_de[i][:2],
                "count": day_count,
                "is_today": day == today,
                "is_weekend": i >= 5,
            }
        )
        if day_count > peak_count:
            peak_count = day_count
            peak_day = week_data[-1]

    # Holidays for edge-case detection
    holiday_dates = db.get_holiday_dates(today.year)
    is_holiday_today = today_str in holiday_dates

    return {
        "date": today_str,
        "is_holiday": is_holiday_today,
        "on_duty": on_duty,
        "absences": absences,
        "on_duty_count": len(on_duty),
        "absences_count": len(absences),
        "week_peak": {
            "day": peak_day["weekday_name"] if peak_day else "",
            "date": peak_day["date"] if peak_day else today_str,
            "count": peak_count,
        },
        "week_days": week_data,
    }


# ── Dashboard: Upcoming ───────────────────────────────────────


@app.get(
    "/api/dashboard/upcoming", tags=["Health"], summary="Upcoming schedule entries"
)
def get_dashboard_upcoming():
    """Return next 3 upcoming holidays and birthdays this week."""
    from datetime import date, timedelta

    db = get_db()
    today = date.today()
    today_str = today.isoformat()

    # Next 3 holidays
    all_holidays = db.get_holidays()
    upcoming_holidays = []
    for h in all_holidays:
        h_date = h.get("DATE", "")
        if h_date >= today_str:
            upcoming_holidays.append(
                {
                    "date": h_date,
                    "name": h.get("NAME", ""),
                    "recurring": bool(h.get("INTERVAL", 0)),
                }
            )
    upcoming_holidays.sort(key=lambda x: x["date"])
    upcoming_holidays = upcoming_holidays[:3]

    # Also try to expand recurring holidays for current year if no future ones
    if not upcoming_holidays:
        all_holidays_raw = db.get_holidays()
        recurring = [h for h in all_holidays_raw if h.get("INTERVAL") == 1]
        if recurring:
            for h in recurring:
                date_str = h.get("DATE", "")
                if len(date_str) >= 10:
                    try:
                        adjusted = str(today.year) + date_str[4:]
                        if adjusted < today_str:
                            adjusted = str(today.year + 1) + date_str[4:]
                        upcoming_holidays.append(
                            {
                                "date": adjusted,
                                "name": h.get("NAME", ""),
                                "recurring": True,
                            }
                        )
                    except Exception:
                        _logger.debug("Holiday date adjustment failed", exc_info=True)
            upcoming_holidays.sort(key=lambda x: x["date"])
            upcoming_holidays = upcoming_holidays[:3]

    # Birthdays this week (Mon–Sun of current week)
    weekday = today.weekday()  # 0=Mon
    week_start = today - timedelta(days=weekday)
    week_end = week_start + timedelta(days=6)

    employees = db.get_employees(include_hidden=False)
    birthdays_this_week = []
    for emp in employees:
        bday_raw = emp.get("BIRTHDAY", "")
        if not bday_raw or len(bday_raw) < 10:
            continue
        try:
            bday_month = int(bday_raw[5:7])
            bday_day = int(bday_raw[8:10])
            # Check if birthday falls in current week
            bday_this_year = date(today.year, bday_month, bday_day)
            if week_start <= bday_this_year <= week_end:
                name = emp.get("NAME", "")
                firstname = emp.get("FIRSTNAME", "")
                full_name = f"{name}, {firstname}".strip(", ")
                days_until = (bday_this_year - today).days
                birthdays_this_week.append(
                    {
                        "employee_id": emp["ID"],
                        "name": full_name,
                        "short": emp.get("SHORTNAME", ""),
                        "date": bday_raw[:10],
                        "display_date": f"{bday_day:02d}.{bday_month:02d}.",
                        "days_until": days_until,
                    }
                )
        except (ValueError, IndexError):
            continue
    birthdays_this_week.sort(key=lambda x: x["days_until"])

    return {
        "holidays": upcoming_holidays,
        "birthdays_this_week": birthdays_this_week,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
    }


# ── Dashboard: Stats ──────────────────────────────────────────


@app.get("/api/dashboard/stats", tags=["Health"], summary="Dashboard statistics")
def get_dashboard_stats(year: int | None = None, month: int | None = None):
    """Return key statistics: total employees, active shifts this month, vacation days used."""
    import calendar as _cal
    from datetime import date
    from datetime import datetime as _dt

    db = get_db()
    today = date.today()

    # Use requested year/month or fall back to today
    req_year = year if year is not None else today.year
    req_month = month if month is not None else today.month

    if not (1 <= req_month <= 12):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400, detail="Invalid month: must be between 1 and 12"
        )

    # Total employees
    employees = db.get_employees(include_hidden=False)
    total_employees = len(employees)

    # Active shifts (distinct shifts used in MASHI for requested month)
    year_str = f"{req_year:04d}-{req_month:02d}"
    shifts_used_ids = set()
    shifts_this_month = 0
    for r in db._read("MASHI"):
        if r.get("DATE", "").startswith(year_str):
            shifts_this_month += 1
            sid = r.get("SHIFTID")
            if sid:
                shifts_used_ids.add(sid)

    # Vacation days used this year (leave type ENTITLED=1)
    lt_map = {lt["ID"]: lt for lt in db.get_leave_types(include_hidden=True)}
    vacation_ids = {lt_id for lt_id, lt in lt_map.items() if lt.get("ENTITLED")}

    year_prefix = str(req_year)
    vacation_days_used = sum(
        1
        for r in db._read("ABSEN")
        if r.get("DATE", "").startswith(year_prefix)
        and r.get("LEAVETYPID") in vacation_ids
    )

    # Coverage bars: per day of requested month
    num_days = _cal.monthrange(req_year, req_month)[1]
    day_counts: dict = {d: 0 for d in range(1, num_days + 1)}
    for r in db._read("MASHI"):
        d = r.get("DATE", "")
        if d.startswith(year_str):
            try:
                day_num = int(d[8:10])
                day_counts[day_num] = day_counts.get(day_num, 0) + 1
            except (ValueError, IndexError):
                pass

    coverage_by_day = []
    for day_num in range(1, num_days + 1):
        try:
            wd = _dt(req_year, req_month, day_num).weekday()
            is_weekend = wd >= 5
            is_today = (
                req_year == today.year
                and req_month == today.month
                and day_num == today.day
            )
            coverage_by_day.append(
                {
                    "day": day_num,
                    "count": day_counts.get(day_num, 0),
                    "is_weekend": is_weekend,
                    "is_today": is_today,
                    "weekday": wd,
                }
            )
        except ValueError:
            pass

    # Employee shift ranking for the month (top/bottom performers)
    try:
        stats = db.get_statistics(req_year, req_month)
        emp_ranking = []
        for s in stats:
            emp_ranking.append(
                {
                    "employee_id": s.get("employee_id", 0),
                    "employee_name": s.get("employee_name", ""),
                    "employee_short": s.get("employee_short", ""),
                    "shifts_count": s.get("shifts_count", 0),
                    "actual_hours": round(s.get("actual_hours", 0), 1),
                    "target_hours": round(s.get("target_hours", 0), 1),
                    "overtime_hours": round(s.get("overtime_hours", 0), 1),
                }
            )
        emp_ranking.sort(key=lambda x: -x["shifts_count"])
    except Exception:
        emp_ranking = []

    return {
        "total_employees": total_employees,
        "shifts_this_month": shifts_this_month,
        "active_shift_types": len(shifts_used_ids),
        "vacation_days_used": vacation_days_used,
        "coverage_by_day": coverage_by_day,
        "month": req_month,
        "year": req_year,
        "employee_ranking": emp_ranking,
    }


# ── Frontend static files (muss NACH allen /api-Routen stehen!) ──
_FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)

if os.path.isdir(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Unknown /api/* paths must return 404, not the SPA — avoids silent 200 on typos/missing endpoints
        """Serve the React SPA index.html for all unmatched routes."""
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(
                status_code=404, detail=f"Endpoint nicht gefunden: /{full_path}"
            )
        index = os.path.join(_FRONTEND_DIST, "index.html")
        return FileResponse(index)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
