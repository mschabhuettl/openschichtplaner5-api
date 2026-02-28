"""FastAPI application for OpenSchichtplaner5."""
import os
import sys
import time as _startup_time_module
from contextlib import asynccontextmanager
from dotenv import load_dotenv

_APP_START_TIME = _startup_time_module.time()

# Load .env file if present
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from typing import Optional  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

# ── Import shared dependencies ──────────────────────────────────
# These are re-exported here so tests can still do `from api.main import _sessions`
from .dependencies import (  # noqa: E402
    _sessions,
    _DEV_TOKEN,
    _DEV_USER,
    _is_token_valid,
    get_db,
    _logger,
    limiter,
    purge_expired_sessions,
    purge_stale_failed_logins,
)

# ── Dev-mode session ────────────────────────────────────────────
# Only active when SP5_DEV_MODE=true (never in production!)
if os.environ.get('SP5_DEV_MODE', '').lower() in ('1', 'true', 'yes'):
    _sessions[_DEV_TOKEN] = {**_DEV_USER, 'expires_at': None}
    _logger.warning("DEV MODE ACTIVE — dev token enabled (SP5_DEV_MODE=true). Do not use in production!")

# ── Config ──────────────────────────────────────────────────────
DB_PATH = os.environ.get(
    'SP5_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'sp5_db', 'Daten')
)
DB_PATH = os.path.normpath(DB_PATH)

# CORS origins from env
_raw_origins = os.environ.get('ALLOWED_ORIGINS', '')
ALLOWED_ORIGINS = (
    [o.strip() for o in _raw_origins.split(',') if o.strip()]
    or ['http://localhost:5173', 'http://localhost:8000']
)

_OPENAPI_TAGS = [
    {"name": "Health", "description": "System health and version info"},
    {"name": "Auth", "description": "Authentication: login and logout"},
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
    {"name": "Self-Service", "description": "Employee self-service: own wishes, absences, profile"},
    {"name": "Events", "description": "Calendar events and holidays"},
    {"name": "Admin", "description": "Administrative operations (Admin only)"},
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
                _logger.debug("Periodic cleanup: removed %d expired sessions, %d stale lockout entries", sess, logins)
        except Exception as _exc:  # pragma: no cover
            _logger.warning("Periodic cleanup error: %s", _exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
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
    yield
    cleanup_task.cancel()
    _logger.info("SP5 API shutting down — cleaning up resources")


app = FastAPI(
    lifespan=lifespan,
    title="OpenSchichtplaner5 API",
    description=(
        "Open-source REST API for Schichtplaner5 databases.\n\n"
        "## Authentication\n"
        "Most endpoints require an `x-auth-token` header obtained from `POST /api/auth/login`.\n\n"
        "## Roles\n"
        "- **Leser** – read-only access\n"
        "- **Planer** – can write schedules and absences\n"
        "- **Admin** – full access including user and master-data management\n"
    ),
    version="0.3.9",
    openapi_tags=_OPENAPI_TAGS,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "x-auth-token", "Authorization"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Content Security Policy: restrict resource loading to same origin
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    # Additional security headers
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    # Only send HSTS if running in production (check env)
    if os.environ.get('SP5_HSTS', '').lower() in ('1', 'true', 'yes'):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Translate Pydantic validation errors into German user-friendly messages."""
    _TYPE_MSGS = {
        "missing": "Pflichtfeld fehlt",
        "int_parsing": "Muss eine ganze Zahl sein",
        "float_parsing": "Muss eine Zahl sein",
        "bool_parsing": "Muss true oder false sein",
        "string_too_short": "Eingabe zu kurz",
        "string_too_long": "Eingabe zu lang",
        "value_error": "Ungültiger Wert",
        "type_error": "Falscher Datentyp",
    }
    errors = []
    for e in exc.errors():
        field = ".".join(str(loc) for loc in e.get("loc", []) if loc not in ("body", "query", "path"))
        etype = e.get("type", "")
        msg = _TYPE_MSGS.get(etype, e.get("msg", "Ungültiger Wert"))
        if field:
            errors.append(f"{field}: {msg}")
        else:
            errors.append(msg)
    detail = "; ".join(errors) if errors else "Ungültige Eingabe"
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions, log with details, return sanitized 500."""
    import traceback
    _logger.error(
        "Unhandled exception: %s %s | %s | %s",
        request.method, request.url.path,
        type(exc).__name__,
        traceback.format_exc().splitlines()[-1],
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Interner Serverfehler. Bitte versuche es erneut."},
    )


# ── Public paths (no auth required) ────────────────────────────
_PUBLIC_PATHS = {'/api/auth/login', '/api/auth/logout', '/api', '/api/health', '/api/version', '/', '/api/errors'}


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request as structured JSON with timing info and request-ID."""
    import time as _t
    import uuid as _uuid
    import json as _json_mod
    from datetime import datetime as _dt2, timezone as _tz2
    # Generate a short unique request ID for correlating log entries
    req_id = _uuid.uuid4().hex[:8]
    start = _t.time()
    response = await call_next(request)
    duration_ms = round((_t.time() - start) * 1000)
    token = request.headers.get('x-auth-token') or request.query_params.get('token')
    user = _sessions.get(token, {}).get('NAME', '-') if token else '-'
    now = _dt2.now(_tz2.utc)
    ts = now.strftime('%Y-%m-%dT%H:%M:%S.') + f"{now.microsecond // 1000:03d}Z"
    entry = {
        "timestamp": ts,
        "req_id": req_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": duration_ms,
        "user": user,
    }
    _logger.info(_json_mod.dumps(entry, ensure_ascii=False))
    response.headers["X-Request-ID"] = req_id
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require authentication for all /api/* endpoints except public ones."""
    path = request.url.path
    method = request.method
    client_ip = request.client.host if request.client else 'unknown'

    if path in _PUBLIC_PATHS or not path.startswith('/api/'):
        return await call_next(request)
    # SSE endpoint also accepts token as query param (EventSource doesn't support headers)
    token = request.headers.get('x-auth-token') or request.query_params.get('token')
    if not token or not _is_token_valid(token):
        _logger.warning("AUTH 401 | ip=%s method=%s path=%s", client_ip, method, path)
        return JSONResponse(
            status_code=401,
            content={"detail": "Nicht angemeldet"}
        )
    response = await call_next(request)
    if response.status_code == 403:
        user_info = _sessions.get(token, {})
        _logger.warning(
            "AUTH 403 | ip=%s method=%s path=%s user=%s",
            client_ip, method, path, user_info.get('NAME', '?')
        )
    if method in ('POST', 'PUT', 'DELETE') and response.status_code < 400:
        user_info = _sessions.get(token, {})
        _logger.info(
            "WRITE %s | ip=%s path=%s user=%s",
            method, client_ip, path, user_info.get('NAME', '?')
        )
    return response



# ── Changelog Middleware ────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.requests import Request as StarletteRequest  # noqa: E402


class ChangelogMiddleware(BaseHTTPMiddleware):
    """Automatically log CREATE/UPDATE/DELETE actions from the API."""

    _ENTITY_MAP = {
        'employees': 'employee',
        'groups': 'group',
        'shifts': 'shift',
        'leave-types': 'leave_type',
        'holidays': 'holiday',
        'workplaces': 'workplace',
        'schedule': 'schedule',
        'absences': 'absence',
        'users': 'user',
        'extracharges': 'extracharge',
    }

    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        method = request.method
        if method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return response
        if response.status_code >= 300:
            return response
        path = request.url.path
        if 'changelog' in path or 'backup' in path or 'compact' in path:
            return response
        parts = [p for p in path.strip('/').split('/') if p]
        entity = 'unknown'
        entity_id = 0
        if len(parts) >= 2:
            segment = parts[1]
            entity = self._ENTITY_MAP.get(segment, segment.replace('-', '_'))
        if len(parts) >= 3:
            try:
                entity_id = int(parts[2])
            except ValueError:
                entity_id = 0
        action_map = {'POST': 'CREATE', 'PUT': 'UPDATE', 'PATCH': 'UPDATE', 'DELETE': 'DELETE'}
        action = action_map.get(method, method)
        try:
            get_db().log_action(
                user='api',
                action=action,
                entity=entity,
                entity_id=entity_id,
                details=f"{method} {path}",
            )
        except Exception:
            pass
        return response


app.add_middleware(ChangelogMiddleware)


# RequestLoggingMiddleware removed — duplicate of request_logging_middleware above

# ── Include routers ─────────────────────────────────────────────
from .routers import auth, employees, schedule, absences, master_data, reports, admin, misc, events  # noqa: E402

app.include_router(auth.router)
app.include_router(employees.router)
app.include_router(schedule.router)
app.include_router(absences.router)
app.include_router(master_data.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(misc.router)
app.include_router(events.router)


# ── Routes ──────────────────────────────────────────────────────

_API_VERSION = "0.3.9"


@app.get(
    "/api/health",
    tags=["Health"],
    summary="Health check",
    description=(
        "Returns service status, API version, uptime in seconds, and DB connection state. "
        "This endpoint is public (no authentication required)."
    ),
)
def health():
    """Health check endpoint — public, no auth required.
    Returns minimal info only. Sensitive details (DB path, logs, cache) are admin-only.
    """
    import time as _t
    db_status = "connected"
    try:
        db = get_db()
        db.get_stats()
    except Exception:
        db_status = "error"

    return {
        "status": "ok",
        "version": _API_VERSION,
        "uptime_seconds": round(_t.time() - _APP_START_TIME, 1),
        "db": {"status": db_status},
    }


@app.get(
    "/api/version",
    tags=["Health"],
    summary="API version",
    description="Returns the current API version string.",
)
def version():
    """Return current API version — public, no auth required."""
    return {"version": _API_VERSION, "service": "OpenSchichtplaner5 API"}


@app.get("/api", tags=["Health"], summary="API root", description="Returns basic service info.")
def root():
    return {"service": "OpenSchichtplaner5 API", "version": _API_VERSION, "backend": "dbf"}

@app.get("/", include_in_schema=False)
async def frontend_root():
    """Serve the React frontend."""
    _dist = os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'dist')
    )
    index = os.path.join(_dist, 'index.html')
    if os.path.exists(index):
        return FileResponse(index)
    return {"service": "OpenSchichtplaner5 API", "version": _API_VERSION}

@app.get("/api/stats", tags=["Health"], summary="Database statistics")
def get_stats():
    return get_db().get_stats()


# ── Dashboard Summary ────────────────────────────────────────

@app.get("/api/dashboard/summary", tags=["Health"], summary="Dashboard summary")
def get_dashboard_summary(
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
    month: Optional[int] = Query(None, description="Month (1-12), defaults to current month"),
):
    """Return all KPIs needed for the Dashboard in one request."""
    import calendar as _cal
    from datetime import date, timedelta, datetime as _dt
    from collections import defaultdict
    from sp5lib.color_utils import bgr_to_hex

    _today = date.today()
    if year is None:
        year = _today.year
    if month is None:
        month = _today.month

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")

    db = get_db()
    today = date.today()
    today_str = today.isoformat()
    prefix = f"{year:04d}-{month:02d}"

    # ── Month label ───────────────────────────────────────────
    month_names_de = [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
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
        1 for d in range(1, num_days + 1)
        if _dt(year, month, d).weekday() < 5
    )
    max_possible = total_employees * working_days if working_days > 0 else 1
    coverage_pct = (
        round((total_shifts_scheduled / max_possible) * 100)
        if max_possible > 0 else 0
    )

    # ── Absences this month ───────────────────────────────────
    lt_map = {lt["ID"]: lt for lt in db.get_leave_types(include_hidden=True)}
    abs_by_type: dict = defaultdict(lambda: {"count": 0, "name": "", "color": "#6B7280"})
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
                zeitkonto_alerts.append({
                    "employee": s["employee_name"],
                    "employee_short": s["employee_short"],
                    "hours_diff": round(s["overtime_hours"], 1),
                })
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
                upcoming_birthdays.append({
                    "name": name,
                    "date": bday_raw[5:],  # MM-DD
                    "days_until": days_until,
                })
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
                        staffing_warnings.append({
                            "date": check_str,
                            "shift": req.get("shift_short") or req.get("shift_name", "?"),
                            "shift_name": req.get("shift_name", "?"),
                            "actual": actual,
                            "required": min_req,
                            "color": req.get("color_bk", "#EF4444"),
                        })
        staffing_warnings.sort(key=lambda x: x["date"])
    except Exception:
        pass

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
    shifts_map = {s['ID']: s for s in db.get_shifts(include_hidden=True)}

    # Helper: get startend for a shift on a given weekday
    def get_shift_startend(shift_id: int, weekday: int) -> str:
        shift = shifts_map.get(shift_id)
        if not shift:
            return ''
        key = f'STARTEND{weekday}'
        return shift.get(key, shift.get('STARTEND0', ''))

    entries = db.get_schedule_day(today_str)
    on_duty = []
    absences = []

    for e in entries:
        kind = e.get('kind')
        if kind in ('shift', 'special_shift'):
            # Prefer SPSHI startend if available, else look up from SHIFT table
            startend = e.get('spshi_startend', '')
            if not startend and e.get('shift_id'):
                startend = get_shift_startend(e['shift_id'], today_weekday)
            on_duty.append({
                'employee_id': e['employee_id'],
                'employee_name': e['employee_name'],
                'employee_short': e['employee_short'],
                'shift_name': e['shift_name'] or e.get('display_name', ''),
                'shift_short': e['shift_short'] or e.get('display_name', ''),
                'color_bk': e['color_bk'],
                'color_text': e['color_text'],
                'workplace_name': e.get('workplace_name', ''),
                'startend': startend,
            })
        elif kind == 'absence':
            absences.append({
                'employee_id': e['employee_id'],
                'employee_name': e['employee_name'],
                'employee_short': e['employee_short'],
                'leave_name': e['leave_name'],
                'color_bk': e['color_bk'],
                'color_text': e['color_text'],
            })

    # ── Week Peak: find busiest day this week ─────────────────
    week_start = today - timedelta(days=today_weekday)  # Monday
    week_days_de = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
    week_data = []
    peak_count = 0
    peak_day = None

    for i in range(7):
        day = week_start + timedelta(days=i)
        day_entries = db.get_schedule_day(day.isoformat()) if day != today else entries
        day_count = sum(1 for e in day_entries if e.get('kind') in ('shift', 'special_shift'))
        week_data.append({
            'date': day.isoformat(),
            'weekday_name': week_days_de[i],
            'weekday_short': week_days_de[i][:2],
            'count': day_count,
            'is_today': day == today,
            'is_weekend': i >= 5,
        })
        if day_count > peak_count:
            peak_count = day_count
            peak_day = week_data[-1]

    # Holidays for edge-case detection
    holiday_dates = db.get_holiday_dates(today.year)
    is_holiday_today = today_str in holiday_dates

    return {
        'date': today_str,
        'is_holiday': is_holiday_today,
        'on_duty': on_duty,
        'absences': absences,
        'on_duty_count': len(on_duty),
        'absences_count': len(absences),
        'week_peak': {
            'day': peak_day['weekday_name'] if peak_day else '',
            'date': peak_day['date'] if peak_day else today_str,
            'count': peak_count,
        },
        'week_days': week_data,
    }

# ── Dashboard: Upcoming ───────────────────────────────────────

@app.get("/api/dashboard/upcoming", tags=["Health"], summary="Upcoming schedule entries")
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
        h_date = h.get('DATE', '')
        if h_date >= today_str:
            upcoming_holidays.append({
                'date': h_date,
                'name': h.get('NAME', ''),
                'recurring': bool(h.get('INTERVAL', 0)),
            })
    upcoming_holidays.sort(key=lambda x: x['date'])
    upcoming_holidays = upcoming_holidays[:3]

    # Also try to expand recurring holidays for current year if no future ones
    if not upcoming_holidays:
        all_holidays_raw = db.get_holidays()
        recurring = [h for h in all_holidays_raw if h.get('INTERVAL') == 1]
        if recurring:
            for h in recurring:
                date_str = h.get('DATE', '')
                if len(date_str) >= 10:
                    try:
                        adjusted = str(today.year) + date_str[4:]
                        if adjusted < today_str:
                            adjusted = str(today.year + 1) + date_str[4:]
                        upcoming_holidays.append({
                            'date': adjusted,
                            'name': h.get('NAME', ''),
                            'recurring': True,
                        })
                    except Exception:
                        pass
            upcoming_holidays.sort(key=lambda x: x['date'])
            upcoming_holidays = upcoming_holidays[:3]

    # Birthdays this week (Mon–Sun of current week)
    weekday = today.weekday()  # 0=Mon
    week_start = today - timedelta(days=weekday)
    week_end = week_start + timedelta(days=6)

    employees = db.get_employees(include_hidden=False)
    birthdays_this_week = []
    for emp in employees:
        bday_raw = emp.get('BIRTHDAY', '')
        if not bday_raw or len(bday_raw) < 10:
            continue
        try:
            bday_month = int(bday_raw[5:7])
            bday_day = int(bday_raw[8:10])
            # Check if birthday falls in current week
            bday_this_year = date(today.year, bday_month, bday_day)
            if week_start <= bday_this_year <= week_end:
                name = emp.get('NAME', '')
                firstname = emp.get('FIRSTNAME', '')
                full_name = f"{name}, {firstname}".strip(', ')
                days_until = (bday_this_year - today).days
                birthdays_this_week.append({
                    'employee_id': emp['ID'],
                    'name': full_name,
                    'short': emp.get('SHORTNAME', ''),
                    'date': bday_raw[:10],
                    'display_date': f"{bday_day:02d}.{bday_month:02d}.",
                    'days_until': days_until,
                })
        except (ValueError, IndexError):
            continue
    birthdays_this_week.sort(key=lambda x: x['days_until'])

    return {
        'holidays': upcoming_holidays,
        'birthdays_this_week': birthdays_this_week,
        'week_start': week_start.isoformat(),
        'week_end': week_end.isoformat(),
    }

# ── Dashboard: Stats ──────────────────────────────────────────

@app.get("/api/dashboard/stats", tags=["Health"], summary="Dashboard statistics")
def get_dashboard_stats(year: Optional[int] = None, month: Optional[int] = None):
    """Return key statistics: total employees, active shifts this month, vacation days used."""
    from datetime import date
    import calendar as _cal
    from datetime import datetime as _dt
    db = get_db()
    today = date.today()

    # Use requested year/month or fall back to today
    req_year = year if year is not None else today.year
    req_month = month if month is not None else today.month

    if not (1 <= req_month <= 12):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")

    # Total employees
    employees = db.get_employees(include_hidden=False)
    total_employees = len(employees)

    # Active shifts (distinct shifts used in MASHI for requested month)
    year_str = f"{req_year:04d}-{req_month:02d}"
    shifts_used_ids = set()
    shifts_this_month = 0
    for r in db._read('MASHI'):
        if r.get('DATE', '').startswith(year_str):
            shifts_this_month += 1
            sid = r.get('SHIFTID')
            if sid:
                shifts_used_ids.add(sid)

    # Vacation days used this year (leave type ENTITLED=1)
    lt_map = {lt['ID']: lt for lt in db.get_leave_types(include_hidden=True)}
    vacation_ids = {lt_id for lt_id, lt in lt_map.items() if lt.get('ENTITLED')}

    year_prefix = str(req_year)
    vacation_days_used = sum(
        1 for r in db._read('ABSEN')
        if r.get('DATE', '').startswith(year_prefix)
        and r.get('LEAVETYPID') in vacation_ids
    )

    # Coverage bars: per day of requested month
    num_days = _cal.monthrange(req_year, req_month)[1]
    day_counts: dict = {d: 0 for d in range(1, num_days + 1)}
    for r in db._read('MASHI'):
        d = r.get('DATE', '')
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
            is_today = (req_year == today.year and req_month == today.month and day_num == today.day)
            coverage_by_day.append({
                'day': day_num,
                'count': day_counts.get(day_num, 0),
                'is_weekend': is_weekend,
                'is_today': is_today,
                'weekday': wd,
            })
        except ValueError:
            pass

    # Employee shift ranking for the month (top/bottom performers)
    try:
        stats = db.get_statistics(req_year, req_month)
        emp_ranking = []
        for s in stats:
            emp_ranking.append({
                'employee_id': s.get('employee_id', 0),
                'employee_name': s.get('employee_name', ''),
                'employee_short': s.get('employee_short', ''),
                'shifts_count': s.get('shifts_count', 0),
                'actual_hours': round(s.get('actual_hours', 0), 1),
                'target_hours': round(s.get('target_hours', 0), 1),
                'overtime_hours': round(s.get('overtime_hours', 0), 1),
            })
        emp_ranking.sort(key=lambda x: -x['shifts_count'])
    except Exception:
        emp_ranking = []

    return {
        'total_employees': total_employees,
        'shifts_this_month': shifts_this_month,
        'active_shift_types': len(shifts_used_ids),
        'vacation_days_used': vacation_days_used,
        'coverage_by_day': coverage_by_day,
        'month': req_month,
        'year': req_year,
        'employee_ranking': emp_ranking,
    }



# ── Frontend static files (muss NACH allen /api-Routen stehen!) ──
_FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'dist')
)

if os.path.isdir(_FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        index = os.path.join(_FRONTEND_DIST, "index.html")
        return FileResponse(index)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
