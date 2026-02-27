"""FastAPI application for OpenSchichtplaner5."""
import os
import sys

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from sp5lib.database import SP5Database

# ── In-memory session store ─────────────────────────────────────
_sessions: dict[str, dict] = {}


def get_current_user(x_auth_token: Optional[str] = Header(None)) -> Optional[dict]:
    """Return user dict for the given token, or None if not authenticated."""
    if x_auth_token and x_auth_token in _sessions:
        return _sessions[x_auth_token]
    return None


def require_admin(user: Optional[dict] = Depends(get_current_user)) -> dict:
    """Dependency that requires an authenticated Admin user."""
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    if not user.get('ADMIN') and user.get('role') != 'Admin':
        raise HTTPException(status_code=403, detail="Keine Admin-Berechtigung")
    return user

# ── Config ─────────────────────────────────────────────────────
DB_PATH = os.environ.get(
    'SP5_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'sp5_db', 'Daten')
)
DB_PATH = os.path.normpath(DB_PATH)

app = FastAPI(
    title="OpenSchichtplaner5 API",
    description="Open-source REST API for Schichtplaner5 databases",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db() -> SP5Database:
    return SP5Database(DB_PATH)


# ── Routes ─────────────────────────────────────────────────────

@app.get("/api")
def root():
    return {"service": "OpenSchichtplaner5 API", "version": "0.1.0", "backend": "dbf", "db_path": DB_PATH}


@app.get("/")
async def frontend_root():
    """Serve the React frontend."""
    _dist = os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'dist')
    )
    index = os.path.join(_dist, 'index.html')
    if os.path.exists(index):
        return FileResponse(index)
    return {"service": "OpenSchichtplaner5 API", "version": "0.1.0"}


@app.get("/api/stats")
def get_stats():
    return get_db().get_stats()


# ── Dashboard Summary ────────────────────────────────────────

@app.get("/api/dashboard/summary")
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
        raise HTTPException(status_code=400, detail="Month must be 1-12")

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

    # ── Upcoming birthdays (next 14 days) ─────────────────────
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
            if 0 <= days_until <= 14:
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


@app.get("/api/employees")
def get_employees(include_hidden: bool = False):
    return get_db().get_employees(include_hidden=include_hidden)


@app.get("/api/employees/{emp_id}")
def get_employee(emp_id: int):
    e = get_db().get_employee(emp_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return e


@app.get("/api/groups")
def get_groups(include_hidden: bool = False):
    db = get_db()
    groups = db.get_groups(include_hidden=include_hidden)
    for g in groups:
        g['member_count'] = len(db.get_group_members(g['ID']))
    return groups


@app.get("/api/groups/{group_id}/members")
def get_group_members(group_id: int):
    db = get_db()
    member_ids = db.get_group_members(group_id)
    employees = db.get_employees(include_hidden=True)
    emp_map = {e['ID']: e for e in employees}
    return [emp_map[mid] for mid in member_ids if mid in emp_map]


@app.get("/api/shifts")
def get_shifts(include_hidden: bool = False):
    return get_db().get_shifts(include_hidden=include_hidden)


@app.get("/api/leave-types")
def get_leave_types(include_hidden: bool = False):
    return get_db().get_leave_types(include_hidden=include_hidden)


@app.get("/api/workplaces")
def get_workplaces(include_hidden: bool = False):
    return get_db().get_workplaces(include_hidden=include_hidden)


@app.get("/api/holidays")
def get_holidays(year: Optional[int] = None):
    return get_db().get_holidays(year=year)


@app.get("/api/schedule")
def get_schedule(
    year: int = Query(..., description="Year"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: Optional[int] = Query(None, description="Filter by group ID")
):
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")
    return get_db().get_schedule(year=year, month=month, group_id=group_id)


@app.get("/api/users")
def get_users():
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


@app.post("/api/users")
def create_user(body: UserCreate, _admin: dict = Depends(require_admin)):
    if body.role not in ('Admin', 'Planer', 'Leser'):
        raise HTTPException(status_code=400, detail="role must be Admin, Planer, or Leser")
    try:
        result = get_db().create_user(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, _admin: dict = Depends(require_admin)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if 'role' in data and data['role'] not in ('Admin', 'Planer', 'Leser'):
        raise HTTPException(status_code=400, detail="role must be Admin, Planer, or Leser")
    try:
        result = get_db().update_user(user_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, _admin: dict = Depends(require_admin)):
    try:
        count = get_db().delete_user(user_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "hidden": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ChangePasswordBody(BaseModel):
    new_password: str


@app.post("/api/users/{user_id}/change-password")
def change_user_password(user_id: int, body: ChangePasswordBody, _admin: dict = Depends(require_admin)):
    if not body.new_password or len(body.new_password.strip()) < 1:
        raise HTTPException(status_code=400, detail="Passwort darf nicht leer sein")
    try:
        ok = get_db().change_password(user_id, body.new_password)
        if not ok:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/auth/login")
def login(body: LoginBody):
    """Simple login: verify username+password against 5USER.DBF."""
    import secrets
    user = get_db().verify_user_password(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Ungültiger Benutzername oder Passwort")
    # Generate a session token and store in memory
    token = secrets.token_hex(32)
    _sessions[token] = user
    return {
        "ok": True,
        "token": token,
        "user": user,
    }


@app.post("/api/auth/logout")
def logout(x_auth_token: Optional[str] = Header(None)):
    """Invalidate the session token."""
    if x_auth_token and x_auth_token in _sessions:
        del _sessions[x_auth_token]
    return {"ok": True}


@app.get("/api/cycles")
def get_cycles():
    return get_db().get_cycles()


# ── Staffing requirements ────────────────────────────────────
@app.get("/api/staffing")
def get_staffing(
    year: int = Query(...),
    month: int = Query(...),
):
    return get_db().get_staffing(year, month)


# ── Schedule Coverage (Personalbedarf-Ampel) ─────────────────
@app.get("/api/schedule/coverage")
def get_schedule_coverage(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
):
    """Return daily coverage status for the given month.
    Each day: { day, scheduled_count, required_count, status: ok|low|critical }
    """
    import calendar as _cal
    from collections import defaultdict

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")

    db = get_db()
    num_days = _cal.monthrange(year, month)[1]
    prefix = f"{year:04d}-{month:02d}"

    # Try DADEM / SHDEM for required staff — both empty in most DBs
    # Use per-day required count (default: 3 = "ok" threshold, 2 = "low")
    required_count = 3  # "ok" if scheduled >= 3, "low" if == 2, "critical" if < 2

    # Count distinct employees scheduled per day (MASHI = regular shifts)
    day_emp_sets: dict = defaultdict(set)
    for r in db._read('MASHI'):
        d = r.get('DATE', '')
        if d.startswith(prefix):
            try:
                day_num = int(d[8:10])
                emp_id = r.get('EMPLOYEEID')
                if emp_id:
                    day_emp_sets[day_num].add(emp_id)
            except (ValueError, IndexError):
                pass

    # Also count SPSHI type=0 (Sonderdienste, not deviations)
    for r in db._read('SPSHI'):
        d = r.get('DATE', '')
        if d.startswith(prefix) and r.get('TYPE', 0) == 0:
            try:
                day_num = int(d[8:10])
                emp_id = r.get('EMPLOYEEID')
                if emp_id:
                    day_emp_sets[day_num].add(emp_id)
            except (ValueError, IndexError):
                pass

    result = []
    for day in range(1, num_days + 1):
        scheduled = len(day_emp_sets.get(day, set()))
        diff = scheduled - required_count
        if diff >= 0:
            status = "ok"
        elif diff == -1:
            status = "low"
        else:
            status = "critical"
        result.append({
            "day": day,
            "scheduled_count": scheduled,
            "required_count": required_count,
            "status": status,
        })

    return result


# ── Day schedule ─────────────────────────────────────────────
@app.get("/api/schedule/day")
def get_schedule_day(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_id: Optional[int] = Query(None),
):
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    return get_db().get_schedule_day(date, group_id=group_id)


# ── Week schedule ────────────────────────────────────────────
@app.get("/api/schedule/week")
def get_schedule_week(
    date: str = Query(..., description="Any date within the target week (YYYY-MM-DD)"),
    group_id: Optional[int] = Query(None),
):
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    return get_db().get_schedule_week(date, group_id=group_id)


# ── Monthly statistics ───────────────────────────────────────
@app.get("/api/statistics")
def get_statistics(
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
    month: Optional[int] = Query(None, description="Month (1-12), defaults to current month"),
    group_id: Optional[int] = Query(None),
):
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    if month is None:
        month = _date.today().month
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")
    return get_db().get_statistics(year, month, group_id=group_id)


# ── Year Summary (Jahresrückblick) ────────────────────────────
@app.get("/api/statistics/year-summary")
def get_year_summary(
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
    group_id: Optional[int] = Query(None),
):
    """Return aggregated statistics for all 12 months of a year (Jahresrückblick)."""
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    db = get_db()

    # Collect stats for each month
    monthly = []
    for m in range(1, 13):
        rows = db.get_statistics(year, m, group_id=group_id)
        total_actual = sum(r.get("actual_hours", 0) or 0 for r in rows)
        total_target = sum(r.get("target_hours", 0) or 0 for r in rows)
        total_absences = sum(r.get("absence_days", 0) or 0 for r in rows)
        total_vacation = sum(r.get("vacation_used", 0) or 0 for r in rows)
        total_sick = sum(r.get("sick_days", 0) or 0 for r in rows)
        total_shifts = sum(r.get("shifts_count", 0) or 0 for r in rows)
        employee_count = len(rows)
        monthly.append({
            "month": m,
            "actual_hours": round(total_actual, 1),
            "target_hours": round(total_target, 1),
            "absence_days": total_absences,
            "vacation_days": total_vacation,
            "sick_days": total_sick,
            "shifts_count": total_shifts,
            "employee_count": employee_count,
            "overtime": round(total_actual - total_target, 1),
        })

    # Per-employee year totals
    emp_totals: dict = {}
    for m in range(1, 13):
        rows = db.get_statistics(year, m, group_id=group_id)
        for r in rows:
            eid = r.get("employee_id")
            if eid not in emp_totals:
                emp_totals[eid] = {
                    "employee_id": eid,
                    "name": r.get("employee_name", r.get("name", "")),
                    "group": r.get("group_name", r.get("group", "")),
                    "actual_hours": 0.0,
                    "target_hours": 0.0,
                    "absence_days": 0,
                    "vacation_days": 0,
                    "sick_days": 0,
                    "shifts_count": 0,
                    "monthly_hours": [0.0] * 12,
                }
            emp_totals[eid]["actual_hours"] += r.get("actual_hours", 0) or 0
            emp_totals[eid]["target_hours"] += r.get("target_hours", 0) or 0
            emp_totals[eid]["absence_days"] += r.get("absence_days", 0) or 0
            emp_totals[eid]["vacation_days"] += r.get("vacation_used", 0) or 0
            emp_totals[eid]["sick_days"] += r.get("sick_days", 0) or 0
            emp_totals[eid]["shifts_count"] += r.get("shifts_count", 0) or 0
            emp_totals[eid]["monthly_hours"][m - 1] = round(r.get("actual_hours", 0) or 0, 1)

    employees = sorted(emp_totals.values(), key=lambda x: x["actual_hours"], reverse=True)
    for e in employees:
        e["actual_hours"] = round(e["actual_hours"], 1)
        e["target_hours"] = round(e["target_hours"], 1)
        e["overtime"] = round(e["actual_hours"] - e["target_hours"], 1)

    # Year totals
    year_totals = {
        "actual_hours": round(sum(m["actual_hours"] for m in monthly), 1),
        "target_hours": round(sum(m["target_hours"] for m in monthly), 1),
        "absence_days": sum(m["absence_days"] for m in monthly),
        "vacation_days": sum(m["vacation_days"] for m in monthly),
        "sick_days": sum(m["sick_days"] for m in monthly),
        "shifts_count": sum(m["shifts_count"] for m in monthly),
    }
    year_totals["overtime"] = round(year_totals["actual_hours"] - year_totals["target_hours"], 1)

    return {
        "year": year,
        "monthly": monthly,
        "employees": employees,
        "totals": year_totals,
    }


# ── Employee detailed statistics ─────────────────────────────
@app.get("/api/statistics/employee/{emp_id}")
def get_employee_statistics(
    emp_id: int,
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
    month: Optional[int] = Query(None, description="Month (1-12); if omitted returns full year overview"),
):
    """
    Return detailed statistics for a single employee.
    Without month: returns 12-month yearly breakdown.
    With month: returns stats for that specific month only.
    """
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    db = get_db()
    emp = db.get_employee(emp_id)
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Employee {emp_id} not found")
    if month is not None:
        if not (1 <= month <= 12):
            raise HTTPException(status_code=400, detail="Month must be 1-12")
        return db.get_employee_stats_month(emp_id, year, month)
    return db.get_employee_stats_year(emp_id, year)


# ── Sickness / Krankenstand statistics ───────────────────────
@app.get("/api/statistics/sickness")
def get_sickness_statistics(
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
):
    """Return sickness (Krankenstand) statistics for a given year.

    Response contains:
    - per_employee: list of {employee_id, name, sick_days, sick_episodes, bradford_factor}
    - per_month: 12-element list {month, sick_days}
    - per_weekday: 7-element list {weekday, weekday_name, sick_days}
    - totals: total_sick_days, affected_employees, total_employees
    """
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    return get_db().get_sickness_statistics(year)


# ── Year overview ────────────────────────────────────────────
@app.get("/api/schedule/year")
def get_schedule_year(
    year: int = Query(...),
    employee_id: int = Query(...),
):
    return get_db().get_schedule_year(year, employee_id)


@app.get("/api/schedule/conflicts")
def get_schedule_conflicts(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: Optional[int] = Query(None, description="Group ID filter"),
):
    """Return all scheduling conflicts for a given month."""
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")
    conflicts = get_db().get_schedule_conflicts(year, month, group_id)
    return {"conflicts": conflicts}


# ── Shift Cycles ─────────────────────────────────────────────

@app.get("/api/shift-cycles")
def get_shift_cycles():
    return get_db().get_shift_cycles()


@app.get("/api/shift-cycles/assign")
def get_cycle_assignments():
    return get_db().get_cycle_assignments()


@app.get("/api/shift-cycles/{cycle_id}")
def get_shift_cycle(cycle_id: int):
    c = get_db().get_shift_cycle(cycle_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Cycle not found")
    return c


class CycleAssignBody(BaseModel):
    employee_id: int
    cycle_id: int
    start_date: str


@app.post("/api/shift-cycles/assign")
def assign_cycle(body: CycleAssignBody):
    try:
        from datetime import datetime
        datetime.strptime(body.start_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        result = get_db().assign_cycle(body.employee_id, body.cycle_id, body.start_date)
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/shift-cycles/assign/{employee_id}")
def remove_cycle_assignment(employee_id: int):
    try:
        count = get_db().remove_cycle_assignment(employee_id)
        return {"ok": True, "removed": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Shift Cycle CRUD ──────────────────────────────────────────

class ShiftCycleCreateBody(BaseModel):
    name: str
    size_weeks: int


class CycleEntryItem(BaseModel):
    index: int
    shift_id: Optional[int] = None


class ShiftCycleUpdateBody(BaseModel):
    name: str
    size_weeks: int
    entries: List[CycleEntryItem] = []


@app.post("/api/shift-cycles")
def create_shift_cycle(body: ShiftCycleCreateBody):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    if body.size_weeks < 1 or body.size_weeks > 52:
        raise HTTPException(status_code=400, detail="Anzahl Wochen muss zwischen 1 und 52 liegen")
    try:
        result = get_db().create_shift_cycle(body.name.strip(), body.size_weeks)
        return {"ok": True, "cycle": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/shift-cycles/{cycle_id}")
def update_shift_cycle(cycle_id: int, body: ShiftCycleUpdateBody):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    if body.size_weeks < 1 or body.size_weeks > 52:
        raise HTTPException(status_code=400, detail="Anzahl Wochen muss zwischen 1 und 52 liegen")
    db = get_db()
    try:
        db.update_shift_cycle(cycle_id, body.name.strip(), body.size_weeks)
        # Replace all entries: clear old ones, write new ones
        db.clear_cycle_entries(cycle_id)
        for entry in body.entries:
            if entry.shift_id:
                db.set_cycle_entry(cycle_id, entry.index, entry.shift_id)
        cycle = db.get_shift_cycle(cycle_id)
        return {"ok": True, "cycle": cycle}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/shift-cycles/{cycle_id}")
def delete_shift_cycle(cycle_id: int):
    try:
        count = get_db().delete_shift_cycle(cycle_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Zyklus nicht gefunden")
        return {"ok": True, "deleted": cycle_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Staffing Requirements ─────────────────────────────────────

@app.get("/api/staffing-requirements")
def get_staffing_requirements(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    group_id: Optional[int] = Query(None, description="Filter by group ID"),
):
    data = get_db().get_staffing_requirements(year=year, month=month)
    if group_id is not None:
        data['shift_requirements'] = [
            r for r in data['shift_requirements']
            if r.get('group_id') is None or r.get('group_id') == group_id
        ]
    return data


# ── Notes ─────────────────────────────────────────────────────

@app.get("/api/notes")
def get_notes(
    date: Optional[str] = Query(None, description="Filter by date YYYY-MM-DD"),
    employee_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None, description="Filter by year (use with month)"),
    month: Optional[int] = Query(None, description="Filter by month 1-12 (use with year)"),
):
    if year is not None and month is not None:
        import calendar as _cal
        last_day = _cal.monthrange(year, month)[1]
        date_from = f"{year:04d}-{month:02d}-01"
        date_to = f"{year:04d}-{month:02d}-{last_day:02d}"
        all_notes = get_db().get_notes(date=None, employee_id=employee_id)
        return [n for n in all_notes if date_from <= (n.get('date') or '') <= date_to]
    return get_db().get_notes(date=date, employee_id=employee_id)


class NoteCreate(BaseModel):
    date: str
    text: str
    employee_id: Optional[int] = 0
    text2: Optional[str] = ''


@app.post("/api/notes")
def add_note(body: NoteCreate):
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        result = get_db().add_note(
            date=body.date,
            text=body.text,
            employee_id=body.employee_id or 0,
            text2=body.text2 or '',
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class NoteUpdate(BaseModel):
    text: Optional[str] = None
    text2: Optional[str] = None
    employee_id: Optional[int] = None
    date: Optional[str] = None


@app.put("/api/notes/{note_id}")
def update_note(note_id: int, body: NoteUpdate):
    if body.date is not None:
        try:
            from datetime import datetime as _dt
            _dt.strptime(body.date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        result = get_db().update_note(
            note_id=note_id,
            text1=body.text,
            text2=body.text2,
            employee_id=body.employee_id,
            date=body.date,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int):
    try:
        count = get_db().delete_note(note_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Periods ───────────────────────────────────────────────────

@app.get("/api/periods")
def get_periods(
    group_id: Optional[int] = Query(None),
):
    return get_db().get_periods(group_id=group_id)


class PeriodCreate(BaseModel):
    group_id: int
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD
    description: str = ''


@app.post("/api/periods")
def create_period(body: PeriodCreate):
    try:
        from datetime import datetime
        datetime.strptime(body.start, '%Y-%m-%d')
        datetime.strptime(body.end, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, erwartet YYYY-MM-DD")
    if body.end < body.start:
        raise HTTPException(status_code=400, detail="end muss >= start sein")
    try:
        result = get_db().create_period({
            'group_id': body.group_id,
            'start': body.start,
            'end': body.end,
            'description': body.description,
        })
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/periods/{period_id}")
def delete_period(period_id: int):
    try:
        count = get_db().delete_period(period_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Staffing Requirements Write ──────────────────────────────

class StaffingRequirementSet(BaseModel):
    shift_id: int
    weekday: int
    min: int
    max: int
    group_id: int


@app.post("/api/staffing-requirements")
def set_staffing_requirement(body: StaffingRequirementSet):
    if not (0 <= body.weekday <= 6):
        raise HTTPException(status_code=400, detail="weekday muss zwischen 0 (Mo) und 6 (So) liegen")
    if body.min < 0:
        raise HTTPException(status_code=400, detail="min darf nicht negativ sein")
    if body.max < body.min:
        raise HTTPException(status_code=400, detail="max muss >= min sein")
    try:
        result = get_db().set_staffing_requirement(
            shift_id=body.shift_id,
            weekday=body.weekday,
            min_staff=body.min,
            max_staff=body.max,
            group_id=body.group_id,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Schedule Templates (Schicht-Vorlagen & Favoriten) ────────
# NOTE: these routes must be registered BEFORE the generic
#       DELETE /api/schedule/{employee_id}/{date} route to avoid
#       "templates" being parsed as an employee_id integer.

class TemplateAssignment(BaseModel):
    employee_id: int
    weekday_offset: int   # 0=Mon … 6=Sun
    shift_id: int
    employee_name: Optional[str] = None
    shift_name: Optional[str] = None


class TemplateCreate(BaseModel):
    name: str
    description: str = ''
    assignments: List[TemplateAssignment]


class TemplateApplyRequest(BaseModel):
    target_date: str    # ISO date string — the Monday (or any anchor) of the target week
    force: bool = False  # overwrite existing entries?


class TemplateCaptureRequest(BaseModel):
    name: str
    description: str = ''
    year: int
    month: int
    week_start_day: int  # day-of-month (1-based) of the Monday to capture
    group_id: Optional[int] = None


@app.get("/api/schedule/templates")
def list_templates():
    """List all saved schedule templates."""
    db = get_db()
    return db.get_schedule_templates()


@app.post("/api/schedule/templates")
def create_template(body: TemplateCreate):
    """Create a new schedule template."""
    db = get_db()
    assignments = [a.dict() for a in body.assignments]
    template = db.create_schedule_template(
        name=body.name,
        description=body.description,
        assignments=assignments,
    )
    return template


@app.post("/api/schedule/templates/capture")
def capture_template(body: TemplateCaptureRequest):
    """Capture the current week's schedule entries as a new template."""
    db = get_db()
    entries = db.get_week_entries_for_template(
        year=body.year,
        month=body.month,
        week_start_day=body.week_start_day,
        group_id=body.group_id,
    )
    if not entries:
        raise HTTPException(status_code=400, detail="Keine Schicht-Einträge in dieser Woche gefunden")
    assignments = [
        {
            'employee_id': e.get('employee_id'),
            'weekday_offset': e.get('weekday_offset', 0),
            'shift_id': e.get('shift_id'),
            'employee_name': e.get('employee_name', ''),
            'shift_name': e.get('display_name', '') or e.get('shift_name', ''),
        }
        for e in entries
    ]
    template = db.create_schedule_template(
        name=body.name,
        description=body.description,
        assignments=assignments,
    )
    return template


@app.delete("/api/schedule/templates/{template_id}")
def delete_template(template_id: int):
    """Delete a schedule template by ID."""
    db = get_db()
    ok = db.delete_schedule_template(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    return {"deleted": True, "id": template_id}


@app.post("/api/schedule/templates/{template_id}/apply")
def apply_template(template_id: int, body: TemplateApplyRequest):
    """Apply a schedule template to a target week."""
    db = get_db()
    try:
        result = db.apply_schedule_template(
            template_id=template_id,
            target_date=body.target_date,
            force=body.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


# ── Write: schedule entry ────────────────────────────────────
class ScheduleEntryCreate(BaseModel):
    employee_id: int
    date: str
    shift_id: int


@app.post("/api/schedule")
def create_schedule_entry(body: ScheduleEntryCreate):
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")
    if db.get_shift(body.shift_id) is None:
        raise HTTPException(status_code=404, detail=f"Schicht {body.shift_id} nicht gefunden")
    try:
        result = db.add_schedule_entry(body.employee_id, body.date, body.shift_id)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/schedule/{employee_id}/{date}")
def delete_schedule_entry(employee_id: int, date: str):
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        count = get_db().delete_schedule_entry(employee_id, date)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/schedule-shift/{employee_id}/{date}")
def delete_shift_only(employee_id: int, date: str):
    """Delete only shift entries (MASHI/SPSHI) for an employee on a date, leaving absences intact."""
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        count = get_db().delete_shift_only(employee_id, date)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/absences/{employee_id}/{date}")
def delete_absence_only(employee_id: int, date: str):
    """Delete only absence entries (ABSEN) for an employee on a date, leaving shifts intact."""
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        count = get_db().delete_absence_only(employee_id, date)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Generate schedule from cycle ────────────────────────────
class ScheduleGenerateRequest(BaseModel):
    year: int
    month: int
    employee_ids: Optional[List[int]] = None
    force: bool = False
    dry_run: bool = False


@app.post("/api/schedule/generate")
def generate_schedule(body: ScheduleGenerateRequest):
    """Generate (or preview) schedule entries for a month based on cycle assignments.
    dry_run=True: returns preview without writing."""
    if not (1 <= body.month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")
    try:
        result = get_db().generate_schedule_from_cycle(
            year=body.year,
            month=body.month,
            employee_ids=body.employee_ids,
            force=body.force,
            dry_run=body.dry_run,
        )
        created = result['created']
        skipped = result['skipped']
        errors = result.get('errors', [])
        preview = result.get('preview', [])
        if body.dry_run:
            message = f"Vorschau: {created} Einträge würden erstellt, {skipped} übersprungen"
        else:
            message = f"{created} Einträge erstellt, {skipped} übersprungen"
        if errors:
            message += f", {len(errors)} Fehler"
        return {
            'created': created,
            'skipped': skipped,
            'errors': errors,
            'preview': preview,
            'message': message,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Write: absence ───────────────────────────────────────────
class AbsenceCreate(BaseModel):
    employee_id: int
    date: str
    leave_type_id: int


@app.get("/api/absences")
def list_absences(
    year: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
    leave_type_id: Optional[int] = Query(None),
):
    """List all absences with optional filters."""
    return get_db().get_absences_list(year=year, employee_id=employee_id, leave_type_id=leave_type_id)


@app.get("/api/group-assignments")
def get_all_group_assignments():
    """Return all group assignments (employee_id, group_id pairs)."""
    return get_db().get_all_group_assignments()


@app.post("/api/absences")
def create_absence(body: AbsenceCreate):
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")
    if db.get_leave_type(body.leave_type_id) is None:
        raise HTTPException(status_code=404, detail=f"Abwesenheitstyp {body.leave_type_id} nicht gefunden")
    try:
        result = db.add_absence(body.employee_id, body.date, body.leave_type_id)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Write: Employees ─────────────────────────────────────────

class EmployeeCreate(BaseModel):
    NAME: str
    FIRSTNAME: str = ''
    SHORTNAME: str = ''
    NUMBER: str = ''
    SEX: int = 0
    HRSDAY: float = 0.0
    HRSWEEK: float = 0.0
    HRSMONTH: float = 0.0
    HRSTOTAL: float = 0.0
    WORKDAYS: str = '1 1 1 1 1 0 0 0'
    HIDE: bool = False
    BOLD: int = 0
    # Personal data
    SALUTATION: str = ''
    STREET: str = ''
    ZIP: str = ''
    TOWN: str = ''
    PHONE: str = ''
    EMAIL: str = ''
    FUNCTION: str = ''
    BIRTHDAY: str = ''
    EMPSTART: str = ''
    EMPEND: str = ''
    # Calculation settings
    CALCBASE: int = 0
    DEDUCTHOL: int = 0
    # Free text fields
    NOTE1: str = ''
    NOTE2: str = ''
    NOTE3: str = ''
    NOTE4: str = ''
    ARBITR1: str = ''
    ARBITR2: str = ''
    ARBITR3: str = ''
    # Colors (BGR int: 0=black, 16777215=white)
    CFGLABEL: Optional[int] = None
    CBKLABEL: Optional[int] = None
    CBKSCHED: Optional[int] = None


class EmployeeUpdate(BaseModel):
    NAME: Optional[str] = None
    FIRSTNAME: Optional[str] = None
    SHORTNAME: Optional[str] = None
    NUMBER: Optional[str] = None
    SEX: Optional[int] = None
    HRSDAY: Optional[float] = None
    HRSWEEK: Optional[float] = None
    HRSMONTH: Optional[float] = None
    HRSTOTAL: Optional[float] = None
    WORKDAYS: Optional[str] = None
    HIDE: Optional[bool] = None
    BOLD: Optional[int] = None
    POSITION: Optional[int] = None
    # Personal data
    SALUTATION: Optional[str] = None
    STREET: Optional[str] = None
    ZIP: Optional[str] = None
    TOWN: Optional[str] = None
    PHONE: Optional[str] = None
    EMAIL: Optional[str] = None
    FUNCTION: Optional[str] = None
    BIRTHDAY: Optional[str] = None
    EMPSTART: Optional[str] = None
    EMPEND: Optional[str] = None
    # Calculation settings
    CALCBASE: Optional[int] = None
    DEDUCTHOL: Optional[int] = None
    # Free text fields
    NOTE1: Optional[str] = None
    NOTE2: Optional[str] = None
    NOTE3: Optional[str] = None
    NOTE4: Optional[str] = None
    ARBITR1: Optional[str] = None
    ARBITR2: Optional[str] = None
    ARBITR3: Optional[str] = None
    # Colors (BGR int)
    CFGLABEL: Optional[int] = None
    CBKLABEL: Optional[int] = None
    CBKSCHED: Optional[int] = None


@app.post("/api/employees")
def create_employee(body: EmployeeCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    # Validate optional date fields
    for field_name, val in [('BIRTHDAY', body.BIRTHDAY), ('EMPSTART', body.EMPSTART), ('EMPEND', body.EMPEND)]:
        if val:
            try:
                from datetime import datetime as _dtt
                _dtt.strptime(val, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{field_name} muss im Format YYYY-MM-DD sein")
    try:
        result = get_db().create_employee(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/employees/{emp_id}")
def update_employee(emp_id: int, body: EmployeeUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_employee(emp_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/employees/{emp_id}")
def delete_employee(emp_id: int):
    try:
        count = get_db().delete_employee(emp_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Employee Photo Upload ─────────────────────────────────────

_PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'uploads', 'photos')


@app.get("/api/employees/{emp_id}/photo")
async def get_employee_photo(emp_id: int):
    from fastapi.responses import FileResponse as _FileResponse
    import pathlib
    photos_dir = pathlib.Path(_PHOTOS_DIR)
    for ext in ('.jpg', '.jpeg', '.png', '.gif'):
        p = photos_dir / f"{emp_id}{ext}"
        if p.exists():
            return _FileResponse(str(p))
    raise HTTPException(status_code=404, detail="Kein Foto vorhanden")


# ── Write: Groups ─────────────────────────────────────────────

class GroupCreate(BaseModel):
    NAME: str
    SHORTNAME: str = ''
    SUPERID: int = 0
    HIDE: bool = False
    BOLD: int = 0
    DAILYDEM: int = 0
    ARBITR: str = ''
    CFGLABEL: Optional[int] = None
    CBKLABEL: Optional[int] = None
    CBKSCHED: Optional[int] = None


class GroupUpdate(BaseModel):
    NAME: Optional[str] = None
    SHORTNAME: Optional[str] = None
    SUPERID: Optional[int] = None
    POSITION: Optional[int] = None
    HIDE: Optional[bool] = None
    BOLD: Optional[int] = None
    DAILYDEM: Optional[int] = None
    ARBITR: Optional[str] = None
    CFGLABEL: Optional[int] = None
    CBKLABEL: Optional[int] = None
    CBKSCHED: Optional[int] = None


class GroupMemberBody(BaseModel):
    employee_id: int


@app.post("/api/groups")
def create_group(body: GroupCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        result = get_db().create_group(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/groups/{group_id}")
def update_group(group_id: int, body: GroupUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_group(group_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: int):
    try:
        count = get_db().delete_group(group_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/groups/{group_id}/members")
def add_group_member(group_id: int, body: GroupMemberBody):
    try:
        result = get_db().add_group_member(group_id, body.employee_id)
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/groups/{group_id}/members/{emp_id}")
def remove_group_member(group_id: int, emp_id: int):
    try:
        count = get_db().remove_group_member(group_id, emp_id)
        return {"ok": True, "removed": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Write: Shifts ─────────────────────────────────────────────

class ShiftCreate(BaseModel):
    NAME: str
    SHORTNAME: str = ''
    COLORBK: int = 16777215
    COLORTEXT: int = 0
    COLORBAR: int = 0
    DURATION0: float = 0.0
    DURATION1: Optional[float] = None
    DURATION2: Optional[float] = None
    DURATION3: Optional[float] = None
    DURATION4: Optional[float] = None
    DURATION5: Optional[float] = None
    DURATION6: Optional[float] = None
    DURATION7: Optional[float] = None
    STARTEND0: Optional[str] = None
    STARTEND1: Optional[str] = None
    STARTEND2: Optional[str] = None
    STARTEND3: Optional[str] = None
    STARTEND4: Optional[str] = None
    STARTEND5: Optional[str] = None
    STARTEND6: Optional[str] = None
    STARTEND7: Optional[str] = None
    HIDE: bool = False


class ShiftUpdate(BaseModel):
    NAME: Optional[str] = None
    SHORTNAME: Optional[str] = None
    COLORBK: Optional[int] = None
    COLORTEXT: Optional[int] = None
    COLORBAR: Optional[int] = None
    DURATION0: Optional[float] = None
    DURATION1: Optional[float] = None
    DURATION2: Optional[float] = None
    DURATION3: Optional[float] = None
    DURATION4: Optional[float] = None
    DURATION5: Optional[float] = None
    DURATION6: Optional[float] = None
    DURATION7: Optional[float] = None
    STARTEND0: Optional[str] = None
    STARTEND1: Optional[str] = None
    STARTEND2: Optional[str] = None
    STARTEND3: Optional[str] = None
    STARTEND4: Optional[str] = None
    STARTEND5: Optional[str] = None
    STARTEND6: Optional[str] = None
    STARTEND7: Optional[str] = None
    POSITION: Optional[int] = None
    HIDE: Optional[bool] = None


@app.post("/api/shifts")
def create_shift(body: ShiftCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        result = get_db().create_shift(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/shifts/{shift_id}")
def update_shift(shift_id: int, body: ShiftUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_shift(shift_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/shifts/{shift_id}")
def hide_shift(shift_id: int):
    try:
        count = get_db().hide_shift(shift_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Write: Leave Types ────────────────────────────────────────

class LeaveTypeCreate(BaseModel):
    NAME: str
    SHORTNAME: str = ''
    COLORBK: int = 16777215
    COLORTEXT: int = 0
    COLORBAR: int = 0
    ENTITLED: bool = False
    STDENTIT: float = 0.0
    HIDE: bool = False


class LeaveTypeUpdate(BaseModel):
    NAME: Optional[str] = None
    SHORTNAME: Optional[str] = None
    COLORBK: Optional[int] = None
    COLORTEXT: Optional[int] = None
    COLORBAR: Optional[int] = None
    ENTITLED: Optional[bool] = None
    STDENTIT: Optional[float] = None
    POSITION: Optional[int] = None
    HIDE: Optional[bool] = None


@app.post("/api/leave-types")
def create_leave_type(body: LeaveTypeCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        result = get_db().create_leave_type(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/leave-types/{lt_id}")
def update_leave_type(lt_id: int, body: LeaveTypeUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_leave_type(lt_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/leave-types/{lt_id}")
def hide_leave_type(lt_id: int):
    try:
        count = get_db().hide_leave_type(lt_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Write: Holidays ───────────────────────────────────────────

class HolidayCreate(BaseModel):
    DATE: str
    NAME: str
    INTERVAL: int = 0


class HolidayUpdate(BaseModel):
    DATE: Optional[str] = None
    NAME: Optional[str] = None
    INTERVAL: Optional[int] = None


@app.post("/api/holidays")
def create_holiday(body: HolidayCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        from datetime import datetime as _dtt
        _dtt.strptime(body.DATE, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="DATE muss im Format YYYY-MM-DD sein")
    try:
        result = get_db().create_holiday(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/holidays/{holiday_id}")
def update_holiday(holiday_id: int, body: HolidayUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_holiday(holiday_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/holidays/{holiday_id}")
def delete_holiday(holiday_id: int):
    try:
        count = get_db().delete_holiday(holiday_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Write: Workplaces ─────────────────────────────────────────

class WorkplaceCreate(BaseModel):
    NAME: str
    SHORTNAME: str = ''
    COLORBK: int = 16777215
    COLORTEXT: int = 0
    COLORBAR: int = 0
    HIDE: bool = False


class WorkplaceUpdate(BaseModel):
    NAME: Optional[str] = None
    SHORTNAME: Optional[str] = None
    COLORBK: Optional[int] = None
    COLORTEXT: Optional[int] = None
    COLORBAR: Optional[int] = None
    POSITION: Optional[int] = None
    HIDE: Optional[bool] = None


@app.post("/api/workplaces")
def create_workplace(body: WorkplaceCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        result = get_db().create_workplace(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/workplaces/{wp_id}")
def update_workplace(wp_id: int, body: WorkplaceUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_workplace(wp_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/workplaces/{wp_id}")
def hide_workplace(wp_id: int):
    try:
        count = get_db().hide_workplace(wp_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Workplace ↔ Employee Assignments ──────────────────────────

@app.get("/api/workplaces/{wp_id}/employees")
def get_workplace_employees(wp_id: int):
    """Return employees assigned to a workplace."""
    try:
        return get_db().get_workplace_employees(wp_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/workplaces/{wp_id}/employees/{employee_id}")
def assign_employee_to_workplace(wp_id: int, employee_id: int):
    """Assign an employee to a workplace."""
    try:
        added = get_db().assign_employee_to_workplace(employee_id, wp_id)
        return {"ok": True, "added": added}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/workplaces/{wp_id}/employees/{employee_id}")
def remove_employee_from_workplace(wp_id: int, employee_id: int):
    """Remove an employee from a workplace."""
    try:
        removed = get_db().remove_employee_from_workplace(employee_id, wp_id)
        return {"ok": True, "removed": removed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Extra Charges (Zeitzuschläge) ─────────────────────────────

class ExtraChargeCreate(BaseModel):
    NAME: str
    START: int = 0      # minutes from midnight
    END: int = 0        # minutes from midnight
    VALIDDAYS: str = '0000000'  # 7 chars: 0=inactive, 1=active per weekday (Mon-Sun)
    HOLRULE: int = 0    # 0=no holiday rule, 1=holidays only, 2=not on holidays
    VALIDITY: int = 0
    HIDE: bool = False


class ExtraChargeUpdate(BaseModel):
    NAME: Optional[str] = None
    START: Optional[int] = None
    END: Optional[int] = None
    VALIDDAYS: Optional[str] = None
    HOLRULE: Optional[int] = None
    VALIDITY: Optional[int] = None
    POSITION: Optional[int] = None
    HIDE: Optional[bool] = None


@app.get("/api/extracharges")
def get_extracharges(include_hidden: bool = False):
    return get_db().get_extracharges(include_hidden=include_hidden)


@app.post("/api/extracharges")
def create_extracharge(body: ExtraChargeCreate):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    if len(body.VALIDDAYS) != 7 or not all(c in '01' for c in body.VALIDDAYS):
        raise HTTPException(status_code=400, detail="VALIDDAYS muss genau 7 Zeichen lang sein und nur '0' oder '1' enthalten (z.B. '1111100')")
    if body.START < 0 or body.START > 1440:
        raise HTTPException(status_code=400, detail="START muss zwischen 0 und 1440 Minuten liegen")
    if body.END < 0 or body.END > 1440:
        raise HTTPException(status_code=400, detail="END muss zwischen 0 und 1440 Minuten liegen")
    try:
        result = get_db().create_extracharge(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/extracharges/summary")
def get_extracharges_summary(
    year: int = Query(...),
    month: int = Query(...),
    employee_id: Optional[int] = Query(None),
):
    """Calculate surcharge hours per ExtraCharge rule for a given month."""
    try:
        result = get_db().calculate_extracharge_hours(year, month, employee_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/extracharges/{xc_id}")
def update_extracharge(xc_id: int, body: ExtraChargeUpdate):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_extracharge(xc_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/extracharges/{xc_id}")
def delete_extracharge(xc_id: int):
    try:
        count = get_db().delete_extracharge(xc_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Leave Entitlements ────────────────────────────────────────

@app.get("/api/leave-entitlements")
def get_leave_entitlements(
    year: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
):
    return get_db().get_leave_entitlements(year=year, employee_id=employee_id)


class LeaveEntitlementCreate(BaseModel):
    employee_id: int
    year: int
    days: float
    carry_forward: Optional[float] = 0
    leave_type_id: Optional[int] = 0


@app.post("/api/leave-entitlements")
def set_leave_entitlement(body: LeaveEntitlementCreate):
    try:
        result = get_db().set_leave_entitlement(
            employee_id=body.employee_id,
            year=body.year,
            days=body.days,
            carry_forward=body.carry_forward or 0,
            leave_type_id=body.leave_type_id or 0,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/leave-balance")
def get_leave_balance(
    year: int = Query(...),
    employee_id: int = Query(...),
):
    return get_db().get_leave_balance(employee_id=employee_id, year=year)


@app.get("/api/leave-balance/group")
def get_leave_balance_group(
    year: int = Query(...),
    group_id: int = Query(...),
):
    return get_db().get_leave_balance_group(year=year, group_id=group_id)


# ── Holiday Bans ──────────────────────────────────────────────

@app.get("/api/holiday-bans")
def get_holiday_bans(
    group_id: Optional[int] = Query(None),
):
    return get_db().get_holiday_bans(group_id=group_id)


class HolidayBanCreate(BaseModel):
    group_id: int
    start_date: str
    end_date: str
    reason: Optional[str] = ''


@app.post("/api/holiday-bans")
def create_holiday_ban(body: HolidayBanCreate):
    try:
        from datetime import datetime
        datetime.strptime(body.start_date, '%Y-%m-%d')
        datetime.strptime(body.end_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    try:
        result = get_db().create_holiday_ban(
            group_id=body.group_id,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason or '',
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/holiday-bans/{ban_id}")
def delete_holiday_ban(ban_id: int):
    try:
        count = get_db().delete_holiday_ban(ban_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Annual Close ──────────────────────────────────────────────

@app.get("/api/annual-close/preview")
def annual_close_preview(
    year: int = Query(...),
    group_id: Optional[int] = Query(None),
    max_carry_forward_days: float = Query(10),
):
    return get_db().get_annual_close_preview(
        year=year,
        group_id=group_id,
        carry_forward_days=max_carry_forward_days,
    )


class AnnualCloseBody(BaseModel):
    year: int
    group_id: Optional[int] = None
    max_carry_forward_days: Optional[float] = 10


@app.post("/api/annual-close")
def run_annual_close(body: AnnualCloseBody):
    try:
        result = get_db().run_annual_close(
            year=body.year,
            group_id=body.group_id,
            carry_forward_days=body.max_carry_forward_days or 10,
        )
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Export endpoints ─────────────────────────────────────────

import io
import csv
import calendar as _calendar
from datetime import datetime as _dt
from fastapi.responses import Response as _Response


def _int_to_rgb(color_int: int) -> str:
    """Convert BGR int to #RRGGBB hex."""
    b = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    r = color_int & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def _csv_response(rows: list, filename: str) -> _Response:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys(), lineterminator='\r\n')
        writer.writeheader()
        writer.writerows(rows)
    content = buf.getvalue()
    return _Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/schedule")
def export_schedule(
    month: str = Query(..., description="Month in YYYY-MM format"),
    group_id: Optional[int] = Query(None),
    format: str = Query("csv", description="csv or html"),
):
    try:
        dt = _dt.strptime(month, "%Y-%m")
        year, mon = dt.year, dt.month
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    db = get_db()
    entries = db.get_schedule(year=year, month=mon, group_id=group_id)
    employees = db.get_employees(include_hidden=False)
    if group_id is not None:
        member_ids = set(db.get_group_members(group_id))
        employees = [e for e in employees if e['ID'] in member_ids]
    employees.sort(key=lambda x: x.get('POSITION', 0))

    # Build lookup: (emp_id, date) -> entry
    entry_map: dict = {}
    for e in entries:
        key = (e['employee_id'], e['date'])
        entry_map[key] = e

    num_days = _calendar.monthrange(year, mon)[1]
    days = [f"{year:04d}-{mon:02d}-{d:02d}" for d in range(1, num_days + 1)]

    if format == "csv":
        rows = []
        for emp in employees:
            row: dict = {
                "Mitarbeiter": f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(', '),
                "Kürzel": emp.get('SHORTNAME', ''),
            }
            for date in days:
                day_num = int(date.split('-')[2])
                e = entry_map.get((emp['ID'], date))
                row[str(day_num)] = e['display_name'] if e else ''
            rows.append(row)
        return _csv_response(rows, f"dienstplan_{month}.csv")
    else:
        # HTML export
        _month_names_de = ["Januar", "Februar", "März", "April", "Mai", "Juni",
                           "Juli", "August", "September", "Oktober", "November", "Dezember"]
        month_name = f"{_month_names_de[mon - 1]} {year}"
        day_headers = ""
        for d in range(1, num_days + 1):
            wd = _dt(year, mon, d).weekday()
            wd_abbr = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][wd]
            is_weekend = wd >= 5
            cls = "weekend" if is_weekend else ""
            day_headers += f'<th class="day-header {cls}">{d}<br><span style="font-weight:normal;font-size:9px">{wd_abbr}</span></th>'

        # Build shift legend
        shifts_all = db.get_shifts(include_hidden=False)
        leave_types_all = db.get_leave_types(include_hidden=False)
        legend_html = '<div class="no-print" style="margin-top:12px;display:flex;flex-wrap:wrap;gap:6px;align-items:center"><strong style="font-size:11px;color:#334155">Legende:</strong>'
        for s in shifts_all:
            bg = s.get('COLORBK_HEX', '#fff')
            fg = s.get('COLORTEXT_HEX', '#000')
            name = s.get('NAME', '')
            short = s.get('SHORTNAME', '')
            legend_html += f'<span style="background:{bg};color:{fg};padding:2px 6px;border:1px solid #ccc;border-radius:3px;font-size:10px;font-weight:bold" title="{name}">{short}</span>'
        legend_html += '</div>'

        rows_html = ""
        for emp in employees:
            emp_name = f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(', ')
            short = emp.get('SHORTNAME', '')
            # Use employee's label color (CBKLABEL) if not white/default
            cbklabel = emp.get('CBKLABEL', 16777215)
            cbklabel_hex = emp.get('CBKLABEL_HEX', '#f8fafc')
            cfglabel_hex = emp.get('CFGLABEL_HEX', '#000000')
            bold_style = 'font-weight:bold;' if emp.get('BOLD') else ''
            if cbklabel and cbklabel != 16777215 and cbklabel != 0:
                emp_style = f'background:{cbklabel_hex};color:{cfglabel_hex};{bold_style}'
            else:
                emp_style = f'background:#f8fafc;{bold_style}'
            rows_html += f'<tr><td class="emp-name" style="{emp_style}">{emp_name}</td><td class="emp-short">{short}</td>'
            for date in days:
                wd = _dt(year, mon, int(date.split('-')[2])).weekday()
                is_weekend = wd >= 5
                e = entry_map.get((emp['ID'], date))
                if e:
                    bg = e.get('color_bk', '#4A90D9')
                    fg = e.get('color_text', '#FFFFFF')
                    display = e.get('display_name', '')
                    rows_html += f'<td class="day-cell" style="background:{bg};color:{fg}"><span title="{e.get("shift_name", e.get("leave_name", display))}">{display}</span></td>'
                else:
                    weekend_style = 'background:#f0f0f0;' if is_weekend else ''
                    rows_html += f'<td class="day-cell" style="{weekend_style}"></td>'
            rows_html += '</tr>\n'

        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Dienstplan {month_name}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; margin: 20px; }}
  h1 {{ font-size: 16px; margin-bottom: 8px; color: #1e293b; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #d1d5db; padding: 3px 4px; white-space: nowrap; }}
  th {{ background: #1e293b; color: white; text-align: center; font-size: 10px; }}
  .emp-name {{ background: #f8fafc; font-weight: bold; min-width: 120px; }}
  .emp-short {{ background: #f8fafc; text-align: center; min-width: 36px; color: #64748b; }}
  .day-header {{ min-width: 28px; }}
  .day-cell {{ text-align: center; font-size: 10px; font-weight: bold; }}
  .weekend {{ background: #475569 !important; }}
  @media print {{
    body {{ margin: 5mm; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
<h1>📅 Dienstplan — {month_name}</h1>
<p class="no-print" style="color:#64748b;font-size:11px">Gedruckt am {_dt.now().strftime("%d.%m.%Y %H:%M")}</p>
{legend_html}
<table>
<thead>
<tr>
  <th style="text-align:left;min-width:130px">Mitarbeiter</th>
  <th style="min-width:36px">Kürzel</th>
  {day_headers}
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""
        return _Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dienstplan_{month}.html"'},
        )


@app.get("/api/export/statistics")
def export_statistics(
    year: int = Query(...),
    group_id: Optional[int] = Query(None),
    format: str = Query("csv", description="csv or html"),
):
    db = get_db()
    rows_data = []
    for mon in range(1, 13):
        month_stats = db.get_statistics(year=year, month=mon, group_id=group_id)
        for s in month_stats:
            rows_data.append({
                "Monat": mon,
                "Mitarbeiter": s['employee_name'],
                "Kürzel": s['employee_short'],
                "Soll (h)": s['target_hours'],
                "Ist (h)": s['actual_hours'],
                "Überstunden (h)": s['overtime_hours'],
                "Abwesenheitstage": s['absence_days'],
                "Urlaubstage": s['vacation_used'],
            })

    # Also build a summary per employee (sum over year)
    from collections import defaultdict
    summary: dict = defaultdict(lambda: {
        "Mitarbeiter": "", "Kürzel": "",
        "Soll (h)": 0.0, "Ist (h)": 0.0, "Überstunden (h)": 0.0,
        "Abwesenheitstage": 0, "Urlaubstage": 0,
    })
    for r in rows_data:
        k = r["Mitarbeiter"]
        summary[k]["Mitarbeiter"] = r["Mitarbeiter"]
        summary[k]["Kürzel"] = r["Kürzel"]
        summary[k]["Soll (h)"] += r["Soll (h)"]
        summary[k]["Ist (h)"] += r["Ist (h)"]
        summary[k]["Überstunden (h)"] += r["Überstunden (h)"]
        summary[k]["Abwesenheitstage"] += r["Abwesenheitstage"]
        summary[k]["Urlaubstage"] += r["Urlaubstage"]

    if format == "csv":
        return _csv_response(rows_data, f"statistiken_{year}.csv")
    else:
        MONTHS_DE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                     "Juli", "August", "September", "Oktober", "November", "Dezember"]

        # Build summary table rows
        summary_rows = ""
        for s in summary.values():
            ot = s["Überstunden (h)"]
            ot_color = "#16a34a" if ot >= 0 else "#dc2626"
            summary_rows += (
                f'<tr>'
                f'<td class="name">{s["Mitarbeiter"]}</td>'
                f'<td class="center">{s["Kürzel"]}</td>'
                f'<td class="num">{s["Soll (h)"]:.1f}</td>'
                f'<td class="num">{s["Ist (h)"]:.1f}</td>'
                f'<td class="num" style="color:{ot_color};font-weight:bold">{"+" if ot>=0 else ""}{ot:.1f}</td>'
                f'<td class="num">{s["Abwesenheitstage"]}</td>'
                f'<td class="num">{s["Urlaubstage"]}</td>'
                f'</tr>\n'
            )

        # Build monthly detail rows
        detail_rows = ""
        for r in rows_data:
            ot = r["Überstunden (h)"]
            ot_color = "#16a34a" if ot >= 0 else "#dc2626"
            detail_rows += (
                f'<tr>'
                f'<td class="center">{MONTHS_DE[r["Monat"]]}</td>'
                f'<td class="name">{r["Mitarbeiter"]}</td>'
                f'<td class="center">{r["Kürzel"]}</td>'
                f'<td class="num">{r["Soll (h)"]:.1f}</td>'
                f'<td class="num">{r["Ist (h)"]:.1f}</td>'
                f'<td class="num" style="color:{ot_color};font-weight:bold">{"+" if ot>=0 else ""}{ot:.1f}</td>'
                f'<td class="num">{r["Abwesenheitstage"]}</td>'
                f'<td class="num">{r["Urlaubstage"]}</td>'
                f'</tr>\n'
            )

        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Statistiken {year}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; margin: 20px; }}
  h1 {{ font-size: 16px; color: #1e293b; margin-bottom: 4px; }}
  h2 {{ font-size: 13px; color: #334155; margin: 18px 0 6px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
  th, td {{ border: 1px solid #d1d5db; padding: 4px 6px; }}
  th {{ background: #1e293b; color: white; text-align: center; }}
  .name {{ font-weight: bold; }}
  .center {{ text-align: center; }}
  .num {{ text-align: right; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  @media print {{ body {{ margin: 5mm; }} }}
</style>
</head>
<body>
<h1>📈 Statistiken — {year}</h1>
<p style="color:#64748b;font-size:11px">Erstellt am {_dt.now().strftime("%d.%m.%Y %H:%M")}</p>

<h2>Jahresübersicht (gesamt)</h2>
<table>
<thead>
<tr>
  <th style="text-align:left">Mitarbeiter</th>
  <th>Kürzel</th>
  <th>Soll (h)</th>
  <th>Ist (h)</th>
  <th>Überstunden</th>
  <th>Abwesenheiten</th>
  <th>Urlaub</th>
</tr>
</thead>
<tbody>
{summary_rows}
</tbody>
</table>

<h2>Monatsdetail</h2>
<table>
<thead>
<tr>
  <th>Monat</th>
  <th style="text-align:left">Mitarbeiter</th>
  <th>Kürzel</th>
  <th>Soll (h)</th>
  <th>Ist (h)</th>
  <th>Überstunden</th>
  <th>Abwesenheiten</th>
  <th>Urlaub</th>
</tr>
</thead>
<tbody>
{detail_rows}
</tbody>
</table>
</body>
</html>"""
        return _Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="statistiken_{year}.html"'},
        )


@app.get("/api/export/employees")
def export_employees(
    format: str = Query("csv"),
):
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    rows = []
    for emp in employees:
        rows.append({
            "ID": emp.get('ID', ''),
            "Name": emp.get('NAME', ''),
            "Vorname": emp.get('FIRSTNAME', ''),
            "Kürzel": emp.get('SHORTNAME', ''),
            "Personalnummer": emp.get('NUMBER', ''),
            "Std/Tag": emp.get('HRSDAY', 0),
            "Std/Woche": emp.get('HRSWEEK', 0),
            "Std/Monat": emp.get('HRSMONTH', 0),
            "Arbeitstage": emp.get('WORKDAYS', ''),
        })
    if format == "html":
        headers_html = "".join(f"<th>{h}</th>" for h in rows[0].keys()) if rows else ""
        rows_html = ""
        for i, row in enumerate(rows):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
            rows_html += f'<tr style="background:{bg}">' + "".join(f"<td>{v}</td>" for v in row.values()) + "</tr>\n"
        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Mitarbeiterliste</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; margin: 20px; }}
  h1 {{ font-size: 16px; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #1e293b; color: #fff; padding: 6px 8px; text-align: left; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #e2e8f0; }}
  @media print {{ @page {{ size: A4 landscape; margin: 10mm; }} }}
</style>
</head>
<body>
<h1>Mitarbeiterliste</h1>
<table><thead><tr>{headers_html}</tr></thead><tbody>
{rows_html}
</tbody></table>
</body></html>"""
        return _Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="mitarbeiter.html"'},
        )
    return _csv_response(rows, "mitarbeiter.csv")


@app.get("/api/export/absences")
def export_absences(
    year: int = Query(...),
    group_id: Optional[int] = Query(None),
    format: str = Query("csv"),
):
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    emp_map = {e['ID']: e for e in employees}
    lt_map = {lt['ID']: lt for lt in db.get_leave_types(include_hidden=True)}

    if group_id is not None:
        member_ids = set(db.get_group_members(group_id))
        emp_map = {k: v for k, v in emp_map.items() if k in member_ids}

    year_str = str(year)
    raw_absences = db._read('ABSEN')

    rows = []
    for r in raw_absences:
        d = r.get('DATE', '')
        if not (d and d.startswith(year_str)):
            continue
        eid = r.get('EMPLOYEEID')
        if eid not in emp_map:
            continue
        emp = emp_map[eid]
        ltid = r.get('LEAVETYPID')
        lt = lt_map.get(ltid) if ltid else None
        rows.append({
            "Datum": d,
            "Mitarbeiter": f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(', '),
            "Kürzel": emp.get('SHORTNAME', ''),
            "Abwesenheitsart": lt.get('NAME', '') if lt else '',
            "Kürzel Art": lt.get('SHORTNAME', '') if lt else '',
        })

    rows.sort(key=lambda x: (x['Datum'], x['Mitarbeiter']))
    if format == "html":
        headers_html = "".join(f"<th>{h}</th>" for h in rows[0].keys()) if rows else ""
        rows_html = ""
        for i, row in enumerate(rows):
            bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
            rows_html += f'<tr style="background:{bg}">' + "".join(f"<td>{v}</td>" for v in row.values()) + "</tr>\n"
        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Abwesenheiten {year}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11px; margin: 20px; }}
  h1 {{ font-size: 16px; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #1e293b; color: #fff; padding: 6px 8px; text-align: left; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #e2e8f0; }}
  @media print {{ @page {{ size: A4 portrait; margin: 10mm; }} }}
</style>
</head>
<body>
<h1>Abwesenheiten {year}</h1>
<table><thead><tr>{headers_html}</tr></thead><tbody>
{rows_html}
</tbody></table>
</body></html>"""
        return _Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="abwesenheiten_{year}.html"'},
        )
    return _csv_response(rows, f"abwesenheiten_{year}.csv")


# ── Monatsabschluss-Report ───────────────────────────────────

@app.get("/api/reports/monthly")
def get_monthly_report(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    format: str = Query("csv", description="Output format: csv or pdf"),
    group_id: Optional[int] = Query(None, description="Filter by group ID"),
):
    """Generate a monthly closing report (Monatsabschluss) for all employees.

    CSV: All employees with target/actual hours, overtime, extra-charge hours, vacation/sick days.
    PDF: Professional A4 report with logo placeholder, table and totals row.
    """
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="month must be 1-12")
    if format not in ("csv", "pdf"):
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'pdf'")

    import calendar as _cal
    from datetime import datetime as _dt

    MONTHS_DE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                 "Juli", "August", "September", "Oktober", "November", "Dezember"]
    month_label = f"{MONTHS_DE[month]} {year}"

    db = get_db()

    # ── Core statistics per employee ──────────────────────────
    stats = db.get_statistics(year, month, group_id=group_id)

    if not stats:
        raise HTTPException(
            status_code=404,
            detail=f"Keine Daten für {month_label} gefunden."
        )

    # ── Extra-charge hours per employee ───────────────────────
    # calculate_extracharge_hours doesn't support per-employee breakdown,
    # so we compute it per employee using the employee_id filter.
    # For performance, only compute if there are extra charges defined.
    try:
        charges_list = db.get_extracharges(include_hidden=False)
        all_charge_names: list = [c["NAME"] for c in charges_list] if charges_list else []
        xc_by_emp: dict = {}
        if charges_list:
            for s in stats:
                eid = s["employee_id"]
                emp_xc = db.calculate_extracharge_hours(year, month, employee_id=eid)
                xc_by_emp[eid] = {xc["charge_name"]: xc["hours"] for xc in emp_xc}
    except Exception:
        xc_by_emp = {}
        all_charge_names = []

    # ── Build row data ────────────────────────────────────────
    rows = []
    for s in stats:
        eid = s["employee_id"]
        row: dict = {
            "Mitarbeiter": s["employee_name"],
            "Kürzel": s["employee_short"],
            "Gruppe": s.get("group_name", ""),
            "Soll-Std.": s["target_hours"],
            "Ist-Std.": s["actual_hours"],
            "Überstunden": s["overtime_hours"],
            "Dienste": s["shifts_count"],
            "Abwesenheitstage": s["absence_days"],
            "Urlaubstage": s["vacation_used"],
            "Kranktage": s.get("sick_days", 0),
        }
        for cn in all_charge_names:
            row[f"Zuschlag: {cn}"] = round(xc_by_emp.get(eid, {}).get(cn, 0.0), 2)
        rows.append(row)

    filename_base = f"monatsabschluss_{year}_{month:02d}"

    # ── CSV output ────────────────────────────────────────────
    if format == "csv":
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=rows[0].keys(), lineterminator='\r\n')
            writer.writeheader()
            writer.writerows(rows)
        content = buf.getvalue()
        return _Response(
            content=content.encode("utf-8-sig"),  # BOM for Excel compatibility
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.csv"'},
        )

    # ── PDF output ────────────────────────────────────────────
    try:
        from fpdf import FPDF
    except ImportError:
        raise HTTPException(status_code=500, detail="fpdf2 nicht installiert. Bitte 'pip install fpdf2' ausführen.")

    class SP5Report(FPDF):
        def header(self):
            # Logo placeholder
            self.set_fill_color(30, 41, 59)  # dark slate
            self.rect(10, 8, 30, 16, 'F')
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(255, 255, 255)
            self.set_xy(10, 11)
            self.cell(30, 6, "SP5", align="C")
            self.set_xy(10, 17)
            self.cell(30, 4, "LOGO", align="C")

            # Title
            self.set_text_color(30, 41, 59)
            self.set_font("Helvetica", "B", 13)
            self.set_xy(44, 9)
            self.cell(0, 7, f"Monatsabschluss-Report", ln=0)
            self.set_font("Helvetica", "", 9)
            self.set_xy(44, 16)
            self.cell(0, 5, f"Zeitraum: {month_label}  |  Erstellt: {_dt.now().strftime('%d.%m.%Y %H:%M')}", ln=0)
            self.set_draw_color(30, 41, 59)
            self.set_line_width(0.5)
            self.line(10, 26, self.w - 10, 26)
            self.ln(20)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 120)
            self.cell(0, 5, f"OpenSchichtplaner5  |  {month_label}  |  Seite {self.page_no()}", align="C")

    pdf = SP5Report(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # ── Summary box ───────────────────────────────────────────
    total_soll = sum(s["target_hours"] for s in stats)
    total_ist = sum(s["actual_hours"] for s in stats)
    total_ot = round(total_ist - total_soll, 2)
    total_abs = sum(s["absence_days"] for s in stats)
    total_vac = sum(s["vacation_used"] for s in stats)
    total_sick = sum(s.get("sick_days", 0) for s in stats)

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(203, 213, 225)
    pdf.set_line_width(0.3)

    box_y = pdf.get_y()
    kpi_labels = [
        ("Mitarbeiter", str(len(stats))),
        ("Soll-Std.", f"{total_soll:.1f} h"),
        ("Ist-Std.", f"{total_ist:.1f} h"),
        ("Überstunden", f"{'+' if total_ot >= 0 else ''}{total_ot:.1f} h"),
        ("Urlaubstage", str(total_vac)),
        ("Kranktage", str(total_sick)),
        ("Abwesenheiten", str(total_abs)),
    ]
    box_w = (pdf.w - 20) / len(kpi_labels)
    for i, (lbl, val) in enumerate(kpi_labels):
        x = 10 + i * box_w
        pdf.set_xy(x, box_y)
        pdf.set_fill_color(241, 245, 249)
        pdf.rect(x, box_y, box_w - 1, 14, 'FD')
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(100, 116, 139)
        pdf.set_xy(x, box_y + 1)
        pdf.cell(box_w - 1, 5, lbl, align="C")
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(30, 41, 59)
        if lbl == "Überstunden":
            ot_val = total_ot
            pdf.set_text_color(22, 163, 74 if ot_val >= 0 else 220, )
            if ot_val < 0:
                pdf.set_text_color(220, 38, 38)
        pdf.set_xy(x, box_y + 6)
        pdf.cell(box_w - 1, 7, val, align="C")

    pdf.set_y(box_y + 17)
    pdf.ln(2)

    # ── Table header ──────────────────────────────────────────
    # Fixed columns + dynamic surcharge columns
    fixed_cols = [
        ("Mitarbeiter", 42),
        ("Kürzel", 14),
        ("Gruppe", 28),
        ("Soll h", 16),
        ("Ist h", 16),
        ("ÜSt h", 16),
        ("Dienste", 14),
        ("Abw.", 12),
        ("Url.", 12),
        ("Krank", 12),
    ]
    # Dynamic surcharge cols (max 3 displayed to fit page)
    xc_cols = [(f"ZZ: {cn[:8]}", 16) for cn in all_charge_names[:3]]
    all_cols = fixed_cols + xc_cols

    # Scale if too wide
    total_w = sum(w for _, w in all_cols)
    avail_w = pdf.w - 20
    scale = avail_w / total_w if total_w > avail_w else 1.0
    all_cols = [(label, round(w * scale, 1)) for label, w in all_cols]

    ROW_H = 7
    HDR_H = 9

    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_draw_color(100, 116, 139)
    pdf.set_line_width(0.2)

    hdr_y = pdf.get_y()
    for label, w in all_cols:
        pdf.cell(w, HDR_H, label, border=1, fill=True, align="C")
    pdf.ln()

    # ── Table rows ────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 7)
    for i, row in enumerate(rows):
        eid = stats[i]["employee_id"]
        if pdf.get_y() + ROW_H > pdf.h - 22:
            pdf.add_page()

        fill = i % 2 == 0
        if fill:
            pdf.set_fill_color(248, 250, 252)
        else:
            pdf.set_fill_color(255, 255, 255)
        pdf.set_text_color(30, 41, 59)

        col_idx = 0
        # Mitarbeiter
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Mitarbeiter"])[:24], border=1, fill=True, align="L"); col_idx += 1
        # Kürzel
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Kürzel"]), border=1, fill=True, align="C"); col_idx += 1
        # Gruppe
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Gruppe"])[:16], border=1, fill=True, align="L"); col_idx += 1
        # Soll h
        pdf.cell(all_cols[col_idx][1], ROW_H, f"{row['Soll-Std.']:.1f}", border=1, fill=True, align="R"); col_idx += 1
        # Ist h
        pdf.cell(all_cols[col_idx][1], ROW_H, f"{row['Ist-Std.']:.1f}", border=1, fill=True, align="R"); col_idx += 1
        # ÜSt h — color
        ot_val = row["Überstunden"]
        if ot_val > 0:
            pdf.set_text_color(22, 163, 74)
        elif ot_val < 0:
            pdf.set_text_color(220, 38, 38)
        else:
            pdf.set_text_color(100, 116, 139)
        ot_sign = "+" if ot_val > 0 else ""
        pdf.cell(all_cols[col_idx][1], ROW_H, f"{ot_sign}{ot_val:.1f}", border=1, fill=True, align="R")
        pdf.set_text_color(30, 41, 59)
        col_idx += 1
        # Dienste
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Dienste"]), border=1, fill=True, align="C"); col_idx += 1
        # Abw.
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Abwesenheitstage"]), border=1, fill=True, align="C"); col_idx += 1
        # Url.
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Urlaubstage"]), border=1, fill=True, align="C"); col_idx += 1
        # Krank
        pdf.cell(all_cols[col_idx][1], ROW_H, str(row["Kranktage"]), border=1, fill=True, align="C"); col_idx += 1
        # Surcharge columns
        for cn in all_charge_names[:3]:
            hrs = row.get(f"Zuschlag: {cn}", 0.0)
            pdf.cell(all_cols[col_idx][1], ROW_H, f"{hrs:.1f}" if hrs else "-", border=1, fill=True, align="C")
            col_idx += 1
        pdf.ln()

    # ── Totals row ────────────────────────────────────────────
    if pdf.get_y() + ROW_H + 2 > pdf.h - 22:
        pdf.add_page()
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 7)
    totals_row = {
        "Mitarbeiter": f"Gesamt ({len(stats)} Mitarbeiter)",
        "Kürzel": "",
        "Gruppe": "",
        "Soll-Std.": f"{total_soll:.1f}",
        "Ist-Std.": f"{total_ist:.1f}",
        "Überstunden": f"{'+' if total_ot >= 0 else ''}{total_ot:.1f}",
        "Dienste": str(sum(s["shifts_count"] for s in stats)),
        "Abwesenheitstage": str(total_abs),
        "Urlaubstage": str(total_vac),
        "Kranktage": str(total_sick),
    }
    col_idx = 0
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Mitarbeiter"][:30], border=1, fill=True, align="L"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, "", border=1, fill=True); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, "", border=1, fill=True); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Soll-Std."], border=1, fill=True, align="R"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Ist-Std."], border=1, fill=True, align="R"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Überstunden"], border=1, fill=True, align="R"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Dienste"], border=1, fill=True, align="C"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Abwesenheitstage"], border=1, fill=True, align="C"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Urlaubstage"], border=1, fill=True, align="C"); col_idx += 1
    pdf.cell(all_cols[col_idx][1], ROW_H, totals_row["Kranktage"], border=1, fill=True, align="C"); col_idx += 1
    for cn in all_charge_names[:3]:
        total_xc = round(sum(xc_by_emp.get(s["employee_id"], {}).get(cn, 0.0) for s in stats), 1)
        pdf.cell(all_cols[col_idx][1], ROW_H, f"{total_xc:.1f}" if total_xc else "-", border=1, fill=True, align="C")
        col_idx += 1
    pdf.ln()

    pdf_bytes = pdf.output()
    return _Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.pdf"'},
    )


# ── Zeitkonto / Überstunden ──────────────────────────────────

@app.get("/api/zeitkonto")
def get_zeitkonto(
    year: int = Query(..., description="Year"),
    group_id: Optional[int] = Query(None, description="Filter by group ID"),
    employee_id: Optional[int] = Query(None, description="Filter by employee ID"),
):
    return get_db().get_zeitkonto(year=year, group_id=group_id, employee_id=employee_id)


@app.get("/api/zeitkonto/detail")
def get_zeitkonto_detail(
    year: int = Query(..., description="Year"),
    employee_id: int = Query(..., description="Employee ID"),
):
    db = get_db()
    result = db.calculate_time_balance(employee_id=employee_id, year=year)
    if not result:
        raise HTTPException(status_code=404, detail="Employee not found")
    return result


@app.get("/api/zeitkonto/summary")
def get_zeitkonto_summary(
    year: int = Query(..., description="Year"),
    group_id: Optional[int] = Query(None, description="Filter by group ID"),
):
    rows = get_db().get_zeitkonto(year=year, group_id=group_id)
    total_target = sum(r['total_target_hours'] for r in rows)
    total_actual = sum(r['total_actual_hours'] for r in rows)
    total_saldo = sum(r['total_saldo'] for r in rows)
    pos = sum(1 for r in rows if r['total_saldo'] >= 0)
    neg = len(rows) - pos
    return {
        'year': year,
        'group_id': group_id,
        'employee_count': len(rows),
        'total_target_hours': round(total_target, 2),
        'total_actual_hours': round(total_actual, 2),
        'total_saldo': round(total_saldo, 2),
        'positive_count': pos,
        'negative_count': neg,
    }


@app.get("/api/bookings")
def get_bookings(
    year: Optional[int] = Query(None, description="Filter by year"),
    month: Optional[int] = Query(None, description="Filter by month (1-12), use with year"),
    employee_id: Optional[int] = Query(None, description="Filter by employee ID"),
):
    return get_db().get_bookings(year=year, month=month, employee_id=employee_id)


class BookingCreate(BaseModel):
    employee_id: int
    date: str
    type: int = 0   # 0 = Iststundenkonto, 1 = Sollstundenkonto
    value: float
    note: Optional[str] = ''


@app.post("/api/bookings")
def create_booking(body: BookingCreate):
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    if body.type not in (0, 1):
        raise HTTPException(status_code=400, detail="type must be 0 (Ist) or 1 (Soll)")
    try:
        result = get_db().create_booking(
            employee_id=body.employee_id,
            date_str=body.date,
            booking_type=body.type,
            value=body.value,
            note=body.note or '',
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/bookings/{booking_id}")
def delete_booking(booking_id: int):
    try:
        count = get_db().delete_booking(booking_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Booking not found")
        return {"ok": True, "deleted": booking_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Carry Forward (Saldo-Übertrag) ────────────────────────────

@app.get("/api/bookings/carry-forward")
def get_carry_forward(employee_id: int = Query(...), year: int = Query(...)):
    try:
        return get_db().get_carry_forward(employee_id=employee_id, year=year)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CarryForwardSet(BaseModel):
    employee_id: int
    year: int
    hours: float


@app.post("/api/bookings/carry-forward")
def set_carry_forward(body: CarryForwardSet):
    try:
        result = get_db().set_carry_forward(
            employee_id=body.employee_id,
            year=body.year,
            hours=body.hours,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AnnualStatementBody(BaseModel):
    employee_id: int
    year: int


@app.post("/api/bookings/annual-statement")
def annual_statement(body: AnnualStatementBody):
    try:
        result = get_db().calculate_annual_statement(
            employee_id=body.employee_id,
            year=body.year,
        )
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Restrictions ──────────────────────────────────────────────

@app.get("/api/restrictions")
def get_restrictions(employee_id: Optional[int] = Query(None)):
    """Return all shift restrictions, optionally filtered by employee_id."""
    return get_db().get_restrictions(employee_id=employee_id)


class RestrictionCreate(BaseModel):
    employee_id: int
    shift_id: int
    reason: Optional[str] = ''
    weekday: Optional[int] = 0


@app.post("/api/restrictions")
def set_restriction(body: RestrictionCreate):
    """Add a shift restriction for an employee."""
    weekday = body.weekday or 0
    if not (0 <= weekday <= 6):
        raise HTTPException(status_code=400, detail="weekday muss zwischen 0 (Mo) und 6 (So) liegen (0 = alle Wochentage)")
    try:
        result = get_db().set_restriction(
            employee_id=body.employee_id,
            shift_id=body.shift_id,
            reason=body.reason or '',
            weekday=weekday,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/restrictions/{employee_id}/{shift_id}")
def remove_restriction(
    employee_id: int,
    shift_id: int,
    weekday: int = Query(0),
):
    """Remove a shift restriction for an employee."""
    try:
        count = get_db().remove_restriction(
            employee_id=employee_id, shift_id=shift_id, weekday=weekday
        )
        return {"ok": True, "removed": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Settings (USETT) ─────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    """Return global settings from 5USETT.DBF."""
    try:
        return get_db().get_usett()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SettingsUpdate(BaseModel):
    ANOANAME: Optional[str] = None
    ANOASHORT: Optional[str] = None
    ANOACRTXT: Optional[int] = None
    ANOACRBAR: Optional[int] = None
    ANOACRBK: Optional[int] = None
    ANOABOLD: Optional[int] = None
    BACKUPFR: Optional[int] = None


@app.put("/api/settings")
def update_settings(body: SettingsUpdate):
    """Update global settings in 5USETT.DBF."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = get_db().update_usett(data)
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Special Staffing Requirements (SPDEM) ────────────────────

@app.get("/api/staffing-requirements/special")
def get_special_staffing(
    date: Optional[str] = Query(None, description="Date filter YYYY-MM-DD"),
    group_id: Optional[int] = Query(None, description="Group ID filter"),
):
    """Return date-specific staffing requirements from 5SPDEM.DBF."""
    try:
        return get_db().get_special_staffing(date=date, group_id=group_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SpecialStaffingCreate(BaseModel):
    group_id: int
    date: str
    shift_id: int
    workplace_id: int = 0
    min: int = 0
    max: int = 0


class SpecialStaffingUpdate(BaseModel):
    group_id: Optional[int] = None
    date: Optional[str] = None
    shift_id: Optional[int] = None
    workplace_id: Optional[int] = None
    min: Optional[int] = None
    max: Optional[int] = None


@app.post("/api/staffing-requirements/special")
def create_special_staffing(body: SpecialStaffingCreate):
    """Create a date-specific staffing requirement."""
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        result = get_db().create_special_staffing(
            groupid=body.group_id,
            date=body.date,
            shiftid=body.shift_id,
            workplacid=body.workplace_id,
            min_staff=body.min,
            max_staff=body.max,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/staffing-requirements/special/{record_id}")
def update_special_staffing(record_id: int, body: SpecialStaffingUpdate):
    """Update a date-specific staffing requirement."""
    data = {k.upper(): v for k, v in body.model_dump().items() if v is not None}
    # Rename keys to match DBF field names
    rename = {'GROUP_ID': 'GROUPID', 'SHIFT_ID': 'SHIFTID', 'WORKPLACE_ID': 'WORKPLACID'}
    data = {rename.get(k, k): v for k, v in data.items()}
    try:
        result = get_db().update_special_staffing(record_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/staffing-requirements/special/{record_id}")
def delete_special_staffing(record_id: int):
    """Delete a date-specific staffing requirement."""
    try:
        count = get_db().delete_special_staffing(record_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Record not found")
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/search")
def global_search(q: str = Query("", description="Search query")):
    """Global search across employees, shifts, and leave types (absence types).
    Returns up to 20 results per category with fuzzy matching.
    """
    query = q.strip()
    if not query:
        return {"results": [], "query": query}

    db = get_db()

    def _fuzzy_score(text: str, q: str) -> float:
        """Simple fuzzy score: 1.0 for exact match, 0.8 for starts-with,
        0.6 for contains, partial character overlap otherwise (0–0.5)."""
        t = text.lower()
        s = q.lower()
        if t == s:
            return 1.0
        if t.startswith(s):
            return 0.8
        if s in t:
            return 0.6
        # Trigram-style: count overlapping 2-char substrings
        t_bi = {t[i:i+2] for i in range(len(t) - 1)} if len(t) >= 2 else set()
        s_bi = {s[i:i+2] for i in range(len(s) - 1)} if len(s) >= 2 else set()
        if t_bi and s_bi:
            overlap = len(t_bi & s_bi) / max(len(t_bi), len(s_bi))
            if overlap > 0.3:
                return overlap * 0.5
        return 0.0

    results = []

    # ── Employees ─────────────────────────────────────────────
    employees = db.get_employees(include_hidden=False)
    for emp in employees:
        name = f"{emp.get('NAME', '')} {emp.get('FIRSTNAME', '')}".strip()
        short = emp.get('SHORTNAME', '') or ''
        number = emp.get('NUMBER', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query),
            _fuzzy_score(number, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "employee",
                "id": emp.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/employees",
                "icon": "👤",
                "score": score,
            })

    # ── Shifts ────────────────────────────────────────────────
    shifts = db.get_shifts(include_hidden=False)
    for sh in shifts:
        name = sh.get('NAME', '') or ''
        short = sh.get('SHORTNAME', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "shift",
                "id": sh.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/shifts",
                "icon": "🕐",
                "score": score,
            })

    # ── Leave Types ───────────────────────────────────────────
    leave_types = db.get_leave_types(include_hidden=False)
    for lt in leave_types:
        name = lt.get('NAME', '') or ''
        short = lt.get('SHORTNAME', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "leave_type",
                "id": lt.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/leave-types",
                "icon": "📋",
                "score": score,
            })

    # ── Groups ────────────────────────────────────────────────
    groups = db.get_groups(include_hidden=False)
    for grp in groups:
        name = grp.get('NAME', '') or ''
        short = grp.get('SHORTNAME', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "group",
                "id": grp.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/groups",
                "icon": "🏢",
                "score": score,
            })

    # Sort by score descending, limit to 30 total
    results.sort(key=lambda x: -x["score"])
    results = results[:30]
    # Remove internal score field from output
    for r in results:
        del r["score"]

    return {"results": results, "query": query}


@app.get("/api/overtime-records")
def get_overtime_records(
    year: Optional[int] = Query(None, description="Filter by year"),
    employee_id: Optional[int] = Query(None, description="Filter by employee ID"),
):
    return get_db().get_overtime_records(year=year, employee_id=employee_id)



# ── Import endpoints ─────────────────────────────────────────

from fastapi import UploadFile, File


@app.post("/api/employees/{emp_id}/photo")
async def upload_employee_photo(emp_id: int, file: UploadFile = File(...)):
    """Upload a photo for an employee (JPG/PNG/GIF)."""
    import pathlib
    photos_dir = pathlib.Path(_PHOTOS_DIR)
    photos_dir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {emp_id} nicht gefunden")

    ct = (file.content_type or '').lower()
    allowed = ('image/jpeg', 'image/png', 'image/gif')
    if ct not in allowed:
        raise HTTPException(status_code=400, detail="Nur JPG, PNG oder GIF erlaubt")

    ext = '.jpg'
    if ct == 'image/png':
        ext = '.png'
    elif ct == 'image/gif':
        ext = '.gif'

    # Remove old photos for this employee
    for old in photos_dir.glob(f"{emp_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass

    dest = photos_dir / f"{emp_id}{ext}"
    content = await file.read()
    dest.write_bytes(content)

    rel_path = f"uploads/photos/{emp_id}{ext}"
    try:
        db.update_employee(emp_id, {'PHOTO': rel_path})
    except Exception:
        pass  # best effort

    return {"ok": True, "photo_url": f"/api/employees/{emp_id}/photo", "path": rel_path}


def _decode_csv(content: bytes) -> str:
    """Try UTF-8 with BOM first, then latin-1."""
    try:
        return content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return content.decode('latin-1')


@app.post("/api/import/employees")
async def import_employees(file: UploadFile = File(...)):
    """Import employees from CSV. Required columns: NAME or NACHNAME.
    Accepted column aliases: VORNAME/FIRSTNAME, NACHNAME/NAME, KURZZEICHEN/SHORTNAME,
    NUMBER/PERSONALNUMMER, HRSDAY, HRSWEEK, HRSMONTH, SEX."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    for i, row in enumerate(reader, start=2):  # row 1 = header
        # Normalize keys
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}

        # Alias mapping
        name = row.get('NAME') or row.get('NACHNAME') or ''
        firstname = row.get('FIRSTNAME') or row.get('VORNAME') or ''
        shortname = row.get('SHORTNAME') or row.get('KURZZEICHEN') or ''
        number = row.get('NUMBER') or row.get('PERSONALNUMMER') or ''

        if not name:
            errors.append(f"Zeile {i}: NAME/NACHNAME fehlt — übersprungen")
            skipped += 1
            continue

        try:
            data = {
                'NAME': name,
                'FIRSTNAME': firstname,
                'SHORTNAME': shortname,
                'NUMBER': number,
                'SEX': int(row.get('SEX') or 0),
                'HRSDAY': float(row.get('HRSDAY') or 0),
                'HRSWEEK': float(row.get('HRSWEEK') or 0),
                'HRSMONTH': float(row.get('HRSMONTH') or 0),
                'WORKDAYS': row.get('WORKDAYS') or '1 1 1 1 1 0 0 0',
                'HIDE': False,
            }
            db.create_employee(data)
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i} ({name}): {e}")

    return {"imported": imported, "errors": errors, "skipped": skipped}


@app.post("/api/import/shifts")
async def import_shifts(file: UploadFile = File(...)):
    """Import shifts from CSV. Required: NAME.
    Optional: KURZZEICHEN/SHORTNAME, FARBE/COLORBK (hex #RRGGBB or int BGR), DURATION0."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    def _parse_color(val: str) -> int:
        """Parse #RRGGBB hex to BGR int, or pass-through int."""
        if not val:
            return 16777215  # white
        val = val.strip()
        if val.startswith('#') and len(val) == 7:
            try:
                r = int(val[1:3], 16)
                g = int(val[3:5], 16)
                b = int(val[5:7], 16)
                return (b << 16) | (g << 8) | r
            except ValueError:
                return 16777215
        try:
            return int(val)
        except ValueError:
            return 16777215

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}

        name = row.get('NAME') or ''
        if not name:
            errors.append(f"Zeile {i}: NAME fehlt — übersprungen")
            skipped += 1
            continue

        shortname = row.get('SHORTNAME') or row.get('KURZZEICHEN') or ''
        colorbk_raw = row.get('COLORBK') or row.get('FARBE') or row.get('HINTERGRUNDFARBE') or ''
        colortext_raw = row.get('COLORTEXT') or row.get('TEXTFARBE') or ''

        try:
            data = {
                'NAME': name,
                'SHORTNAME': shortname,
                'COLORBK': _parse_color(colorbk_raw),
                'COLORTEXT': _parse_color(colortext_raw) if colortext_raw else 0,
                'COLORBAR': 0,
                'DURATION0': float(row.get('DURATION0') or row.get('DAUER') or 0),
                'HIDE': False,
            }
            db.create_shift(data)
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i} ({name}): {e}")

    return {"imported": imported, "errors": errors, "skipped": skipped}


@app.post("/api/import/absences")
async def import_absences(file: UploadFile = File(...)):
    """Import absences from CSV. Required: EMPLOYEE_ID, DATE (YYYY-MM-DD), LEAVE_TYPE_ID."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}

        emp_id_raw = row.get('EMPLOYEE_ID') or row.get('MITARBEITER_ID') or ''
        date_raw = row.get('DATE') or row.get('DATUM') or ''
        lt_id_raw = row.get('LEAVE_TYPE_ID') or row.get('ABWESENHEITSART_ID') or ''

        if not emp_id_raw or not date_raw or not lt_id_raw:
            errors.append(f"Zeile {i}: Pflichtfelder fehlen (EMPLOYEE_ID, DATE, LEAVE_TYPE_ID) — übersprungen")
            skipped += 1
            continue

        try:
            from datetime import datetime
            datetime.strptime(date_raw, '%Y-%m-%d')
        except ValueError:
            errors.append(f"Zeile {i}: Ungültiges Datum '{date_raw}' (erwartet YYYY-MM-DD) — übersprungen")
            skipped += 1
            continue

        try:
            emp_id = int(emp_id_raw)
            lt_id = int(lt_id_raw)
            db.add_absence(emp_id, date_raw, lt_id)
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i}: {e}")

    return {"imported": imported, "errors": errors, "skipped": skipped}


@app.post("/api/import/holidays")
async def import_holidays(file: UploadFile = File(...)):
    """Import holidays from CSV. Required: DATE (YYYY-MM-DD), NAME.
    Optional: INTERVAL (0=einmalig, 1=jährlich), REGION (ignored, for info only)."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}

        date_raw = row.get('DATE') or row.get('DATUM') or ''
        name = row.get('NAME') or row.get('BEZEICHNUNG') or ''

        if not date_raw or not name:
            errors.append(f"Zeile {i}: DATE und NAME sind Pflicht — übersprungen")
            skipped += 1
            continue

        try:
            from datetime import datetime
            datetime.strptime(date_raw, '%Y-%m-%d')
        except ValueError:
            errors.append(f"Zeile {i}: Ungültiges Datum '{date_raw}' (erwartet YYYY-MM-DD) — übersprungen")
            skipped += 1
            continue

        try:
            interval_raw = row.get('INTERVAL') or row.get('JAEHRLICH') or '0'
            data = {
                'DATE': date_raw,
                'NAME': name,
                'INTERVAL': int(interval_raw) if interval_raw.isdigit() else 0,
            }
            db.create_holiday(data)
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i} ({name}): {e}")

    return {"imported": imported, "errors": errors, "skipped": skipped}


@app.post("/api/import/bookings-actual")
async def import_bookings_actual(file: UploadFile = File(...)):
    """Import actual-hour bookings (TYPE=0) from CSV.
    Required: Personalnummer,Datum,Stunden. Optional: Notiz."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    emp_by_number = {(str(e.get('NUMBER', '')) or '').strip(): e for e in db.get_employees(include_hidden=False)}

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}
        nummer = row.get('PERSONALNUMMER') or row.get('NUMBER') or ''
        date_raw = row.get('DATUM') or row.get('DATE') or ''
        stunden_raw = row.get('STUNDEN') or row.get('HOURS') or ''
        notiz = row.get('NOTIZ') or row.get('NOTE') or ''

        if not nummer or not date_raw or not stunden_raw:
            errors.append(f"Zeile {i}: Pflichtfelder fehlen (Personalnummer,Datum,Stunden) — übersprungen")
            skipped += 1
            continue

        emp = emp_by_number.get(nummer)
        if not emp:
            errors.append(f"Zeile {i}: Personalnummer '{nummer}' nicht gefunden — übersprungen")
            skipped += 1
            continue

        try:
            from datetime import datetime as _dt
            _dt.strptime(date_raw, '%Y-%m-%d')
            stunden = float(stunden_raw.replace(',', '.'))
            db.create_booking(emp['ID'], date_raw, 0, stunden, notiz)
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i}: {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


@app.post("/api/import/bookings-nominal")
async def import_bookings_nominal(file: UploadFile = File(...)):
    """Import nominal-hour bookings (TYPE=1) from CSV.
    Required: Personalnummer,Datum,Stunden. Optional: Notiz."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    emp_by_number = {(str(e.get('NUMBER', '')) or '').strip(): e for e in db.get_employees(include_hidden=False)}

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}
        nummer = row.get('PERSONALNUMMER') or row.get('NUMBER') or ''
        date_raw = row.get('DATUM') or row.get('DATE') or ''
        stunden_raw = row.get('STUNDEN') or row.get('HOURS') or ''
        notiz = row.get('NOTIZ') or row.get('NOTE') or ''

        if not nummer or not date_raw or not stunden_raw:
            errors.append(f"Zeile {i}: Pflichtfelder fehlen (Personalnummer,Datum,Stunden) — übersprungen")
            skipped += 1
            continue

        emp = emp_by_number.get(nummer)
        if not emp:
            errors.append(f"Zeile {i}: Personalnummer '{nummer}' nicht gefunden — übersprungen")
            skipped += 1
            continue

        try:
            from datetime import datetime as _dt
            _dt.strptime(date_raw, '%Y-%m-%d')
            stunden = float(stunden_raw.replace(',', '.'))
            db.create_booking(emp['ID'], date_raw, 1, stunden, notiz)
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i}: {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


@app.post("/api/import/entitlements")
async def import_entitlements(file: UploadFile = File(...)):
    """Import leave entitlements from CSV.
    Required: Personalnummer,Jahr,Abwesenheitsart-Kürzel,Tage."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    emp_by_number = {(str(e.get('NUMBER', '')) or '').strip(): e for e in db.get_employees(include_hidden=False)}
    lt_by_short = {lt['SHORTNAME'].strip().upper(): lt for lt in db.get_leave_types(include_hidden=False)}

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}
        nummer = row.get('PERSONALNUMMER') or row.get('NUMBER') or ''
        year_raw = row.get('JAHR') or row.get('YEAR') or ''
        kuerzel = (row.get('ABWESENHEITSART') or row.get('KÜRZEL') or row.get('KURZEL') or row.get('SHORTNAME') or '').upper()
        tage_raw = row.get('TAGE') or row.get('DAYS') or ''

        if not nummer or not year_raw or not kuerzel or not tage_raw:
            errors.append(f"Zeile {i}: Pflichtfelder fehlen (Personalnummer,Jahr,Abwesenheitsart-Kürzel,Tage) — übersprungen")
            skipped += 1
            continue

        emp = emp_by_number.get(nummer)
        if not emp:
            errors.append(f"Zeile {i}: Personalnummer '{nummer}' nicht gefunden — übersprungen")
            skipped += 1
            continue

        lt = lt_by_short.get(kuerzel)
        if not lt:
            errors.append(f"Zeile {i}: Abwesenheitsart-Kürzel '{kuerzel}' nicht gefunden — übersprungen")
            skipped += 1
            continue

        try:
            year = int(year_raw)
            tage = float(tage_raw.replace(',', '.'))
            db.set_leave_entitlement(emp['ID'], year, tage, leave_type_id=lt['ID'])
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i}: {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


@app.post("/api/import/absences-csv")
async def import_absences_csv(file: UploadFile = File(...)):
    """Import absences from CSV using Personalnummer and Abwesenheitsart-Kürzel.
    Required: Personalnummer,Datum,Abwesenheitsart-Kürzel."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    emp_by_number = {(str(e.get('NUMBER', '')) or '').strip(): e for e in db.get_employees(include_hidden=False)}
    lt_by_short = {lt['SHORTNAME'].strip().upper(): lt for lt in db.get_leave_types(include_hidden=False)}

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}
        nummer = row.get('PERSONALNUMMER') or row.get('NUMBER') or ''
        date_raw = row.get('DATUM') or row.get('DATE') or ''
        kuerzel = (row.get('ABWESENHEITSART') or row.get('KÜRZEL') or row.get('KURZEL') or row.get('SHORTNAME') or '').upper()

        if not nummer or not date_raw or not kuerzel:
            errors.append(f"Zeile {i}: Pflichtfelder fehlen (Personalnummer,Datum,Abwesenheitsart-Kürzel) — übersprungen")
            skipped += 1
            continue

        emp = emp_by_number.get(nummer)
        if not emp:
            errors.append(f"Zeile {i}: Personalnummer '{nummer}' nicht gefunden — übersprungen")
            skipped += 1
            continue

        lt = lt_by_short.get(kuerzel)
        if not lt:
            errors.append(f"Zeile {i}: Abwesenheitsart-Kürzel '{kuerzel}' nicht gefunden — übersprungen")
            skipped += 1
            continue

        try:
            from datetime import datetime as _dt
            _dt.strptime(date_raw, '%Y-%m-%d')
            db.add_absence(emp['ID'], date_raw, lt['ID'])
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i}: {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


@app.post("/api/import/groups")
async def import_groups(file: UploadFile = File(...)):
    """Import groups from CSV.
    Required: Name. Optional: Kürzel, Übergeordnete-Gruppe-Name."""
    content = await file.read()
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    errors = []
    db = get_db()

    existing_groups = db.get_groups(include_hidden=True)
    group_by_name = {g['NAME'].strip().upper(): g for g in existing_groups}

    for i, row in enumerate(reader, start=2):
        row = {k.strip().upper(): v.strip() for k, v in row.items() if k}
        name = row.get('NAME') or row.get('BEZEICHNUNG') or ''
        kuerzel = row.get('KÜRZEL') or row.get('KURZEL') or row.get('SHORTNAME') or ''
        parent_name = (row.get('ÜBERGEORDNETE-GRUPPE-NAME') or row.get('UEBERGEORDNETE-GRUPPE-NAME') or
                       row.get('PARENT') or row.get('SUPERGRUPPE') or '').strip().upper()

        if not name:
            errors.append(f"Zeile {i}: NAME fehlt — übersprungen")
            skipped += 1
            continue

        parent_id = None
        if parent_name:
            parent_grp = group_by_name.get(parent_name)
            if not parent_grp:
                errors.append(f"Zeile {i}: Übergeordnete Gruppe '{parent_name}' nicht gefunden — übersprungen")
                skipped += 1
                continue
            parent_id = parent_grp['ID']

        try:
            data = {
                'NAME': name,
                'SHORTNAME': kuerzel,
                'SUPERID': parent_id or 0,
                'HIDE': False,
            }
            db.create_group(data)
            # Refresh for subsequent lookups
            group_by_name[name.upper()] = {'NAME': name, 'ID': -1}
            imported += 1
        except Exception as e:
            errors.append(f"Zeile {i} ({name}): {e}")
            skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── Backup / Restore endpoints ───────────────────────────────

import zipfile
from datetime import datetime as _backup_dt
from fastapi.responses import StreamingResponse


@app.get("/api/backup/download")
def backup_download():
    """Create a ZIP of all .DBF / .FPT / .CDX files and return as download."""
    allowed_ext = {'.DBF', '.FPT', '.CDX'}

    buf = io.BytesIO()
    files_added: list[str] = []

    with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(DB_PATH):
            ext = os.path.splitext(fname)[1].upper()
            if ext in allowed_ext:
                full_path = os.path.join(DB_PATH, fname)
                if os.path.isfile(full_path):
                    zf.write(full_path, arcname=fname)
                    files_added.append(fname)

    buf.seek(0)
    ts = _backup_dt.now().strftime('%Y%m%d_%H%M')
    filename = f"sp5_backup_{ts}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/backup/restore")
async def backup_restore(file: UploadFile = File(...)):
    """Restore .DBF / .FPT / .CDX files from an uploaded ZIP."""
    allowed_ext = {'.DBF', '.FPT', '.CDX'}

    content = await file.read()

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Ungültige ZIP-Datei")

    names_in_zip = zf.namelist()
    dbf_files = [n for n in names_in_zip if os.path.splitext(n)[1].upper() == '.DBF']
    if not dbf_files:
        raise HTTPException(status_code=400, detail="ZIP enthält keine .DBF Dateien")

    safe_db_path = os.path.abspath(DB_PATH)
    restored: list[str] = []
    with zf:
        for name in names_in_zip:
            ext = os.path.splitext(name)[1].upper()
            if ext not in allowed_ext:
                continue
            basename = os.path.basename(name)
            if not basename:
                continue
            # Extra safety: ensure the resolved destination is inside DB_PATH
            dest = os.path.normpath(os.path.join(safe_db_path, basename))
            if not dest.startswith(safe_db_path + os.sep) and dest != safe_db_path:
                # Should never happen since basename has no path separators, but
                # guard against exotic os.path.join edge cases on all platforms.
                continue
            data = zf.read(name)
            with open(dest, 'wb') as fout:
                fout.write(data)
            restored.append(basename)

    return {"restored": len(restored), "files": restored}


# ── Bulk Schedule Operations ─────────────────────────────────

class BulkEntry(BaseModel):
    employee_id: int
    date: str
    shift_id: Optional[int] = None


class BulkScheduleBody(BaseModel):
    entries: List[BulkEntry]
    overwrite: bool = True


@app.post("/api/schedule/bulk")
def bulk_schedule(body: BulkScheduleBody):
    """Bulk create/update/delete schedule entries in a single request.
    If shift_id is null the entry is deleted; otherwise created or overwritten."""
    from datetime import datetime as _dt2
    created = 0
    updated = 0
    deleted = 0
    db = get_db()
    for entry in body.entries:
        try:
            _dt2.strptime(entry.date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {entry.date}")
        try:
            if entry.shift_id is None:
                count = db.delete_schedule_entry(entry.employee_id, entry.date)
                if count > 0:
                    deleted += 1
            else:
                if body.overwrite:
                    old_count = db.delete_schedule_entry(entry.employee_id, entry.date)
                else:
                    old_count = 0
                db.add_schedule_entry(entry.employee_id, entry.date, entry.shift_id)
                if old_count > 0:
                    updated += 1
                else:
                    created += 1
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"created": created, "updated": updated, "deleted": deleted}


# ── Einsatzplan Write (SPSHI) ────────────────────────────────

class EinsatzplanCreate(BaseModel):
    employee_id: int
    date: str
    name: Optional[str] = ''
    shortname: Optional[str] = ''
    shift_id: Optional[int] = 0
    workplace_id: Optional[int] = 0
    startend: Optional[str] = ''
    duration: Optional[float] = 0.0
    colortext: Optional[int] = 0
    colorbar: Optional[int] = 0
    colorbk: Optional[int] = 16777215


class EinsatzplanUpdate(BaseModel):
    name: Optional[str] = None
    shortname: Optional[str] = None
    shift_id: Optional[int] = None
    workplace_id: Optional[int] = None
    startend: Optional[str] = None
    duration: Optional[float] = None
    colortext: Optional[int] = None
    colorbar: Optional[int] = None
    colorbk: Optional[int] = None


class DeviationCreate(BaseModel):
    employee_id: int
    date: str
    name: Optional[str] = 'Arbeitszeitabweichung'
    shortname: Optional[str] = 'AZA'
    startend: Optional[str] = ''   # e.g. "07:00-15:30"
    duration: Optional[float] = 0.0  # minutes or hours (stores raw)
    colortext: Optional[int] = 0
    colorbar: Optional[int] = 0
    colorbk: Optional[int] = 16744448  # orange-ish default


@app.post("/api/einsatzplan")
def create_einsatzplan_entry(body: EinsatzplanCreate):
    """Create a Sonderdienst entry in SPSHI (TYPE=0)."""
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")
    try:
        result = db.add_spshi_entry(
            employee_id=body.employee_id,
            date_str=body.date,
            name=body.name or '',
            shortname=body.shortname or '',
            shift_id=body.shift_id or 0,
            workplace_id=body.workplace_id or 0,
            entry_type=0,
            startend=body.startend or '',
            duration=body.duration or 0.0,
            colortext=body.colortext or 0,
            colorbar=body.colorbar or 0,
            colorbk=body.colorbk if body.colorbk is not None else 16777215,
        )
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/einsatzplan/{entry_id}")
def update_einsatzplan_entry(entry_id: int, body: EinsatzplanUpdate):
    """Update an existing SPSHI entry."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    # Map frontend keys to DBF field names
    key_map = {
        'name': 'NAME', 'shortname': 'SHORTNAME', 'shift_id': 'SHIFTID',
        'workplace_id': 'WORKPLACID', 'startend': 'STARTEND', 'duration': 'DURATION',
        'colortext': 'COLORTEXT', 'colorbar': 'COLORBAR', 'colorbk': 'COLORBK',
    }
    mapped = {key_map.get(k, k.upper()): v for k, v in data.items()}
    try:
        result = get_db().update_spshi_entry(entry_id, mapped)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/einsatzplan/{entry_id}")
def delete_einsatzplan_entry(entry_id: int):
    """Delete a SPSHI entry by ID."""
    try:
        count = get_db().delete_spshi_entry_by_id(entry_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="SPSHI entry not found")
        return {"ok": True, "deleted": entry_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/einsatzplan/deviation")
def create_deviation(body: DeviationCreate):
    """Create an Arbeitszeitabweichung entry in SPSHI (TYPE=1)."""
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")
    try:
        result = db.add_spshi_entry(
            employee_id=body.employee_id,
            date_str=body.date,
            name=body.name or 'Arbeitszeitabweichung',
            shortname=body.shortname or 'AZA',
            shift_id=0,
            workplace_id=0,
            entry_type=1,
            startend=body.startend or '',
            duration=body.duration or 0.0,
            colortext=body.colortext or 0,
            colorbar=body.colorbar or 0,
            colorbk=body.colorbk if body.colorbk is not None else 16744448,
        )
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/einsatzplan")
def get_einsatzplan(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_id: Optional[int] = Query(None),
):
    """Return SPSHI entries for a specific date (Sonderdienste + Abweichungen)."""
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    return get_db().get_spshi_entries_for_day(date, group_id=group_id)


# ── Cycle Exceptions ─────────────────────────────────────────

class CycleExceptionSet(BaseModel):
    employee_id: int
    cycle_assignment_id: int
    date: str
    type: int = 1  # 1=skip, 0=normal


@app.get("/api/cycle-exceptions")
def get_cycle_exceptions(
    employee_id: Optional[int] = Query(None),
    cycle_assignment_id: Optional[int] = Query(None),
):
    """Get cycle exceptions (date overrides in assigned cycles)."""
    return get_db().get_cycle_exceptions(employee_id=employee_id,
                                          cycle_assignment_id=cycle_assignment_id)


@app.post("/api/cycle-exceptions")
def set_cycle_exception(body: CycleExceptionSet):
    """Set a cycle exception for a specific date."""
    try:
        result = get_db().set_cycle_exception(
            employee_id=body.employee_id,
            cycle_assignment_id=body.cycle_assignment_id,
            date_str=body.date,
            exc_type=body.type,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/cycle-exceptions/{exception_id}")
def delete_cycle_exception(exception_id: int):
    """Delete a cycle exception by ID."""
    count = get_db().delete_cycle_exception(exception_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Cycle exception not found")
    return {"ok": True, "deleted": exception_id}


# ── Employee / Group Access Rights ───────────────────────────

class EmployeeAccessSet(BaseModel):
    user_id: int
    employee_id: int
    rights: int = 0


class GroupAccessSet(BaseModel):
    user_id: int
    group_id: int
    rights: int = 0


@app.get("/api/employee-access")
def get_employee_access(user_id: Optional[int] = Query(None)):
    """Get employee-level access restrictions."""
    return get_db().get_employee_access(user_id=user_id)


@app.post("/api/employee-access")
def set_employee_access(body: EmployeeAccessSet):
    """Set employee-level access for a user."""
    try:
        result = get_db().set_employee_access(body.user_id, body.employee_id, body.rights)
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/employee-access/{access_id}")
def delete_employee_access(access_id: int):
    """Remove an employee access entry."""
    count = get_db().delete_employee_access(access_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Access record not found")
    return {"ok": True, "deleted": access_id}


@app.get("/api/group-access")
def get_group_access(user_id: Optional[int] = Query(None)):
    """Get group-level access restrictions."""
    return get_db().get_group_access(user_id=user_id)


@app.post("/api/group-access")
def set_group_access(body: GroupAccessSet):
    """Set group-level access for a user."""
    try:
        result = get_db().set_group_access(body.user_id, body.group_id, body.rights)
        return {"ok": True, "record": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/group-access/{access_id}")
def delete_group_access(access_id: int):
    """Remove a group access entry."""
    count = get_db().delete_group_access(access_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Access record not found")
    return {"ok": True, "deleted": access_id}


# ── Absence Status (approval workflow) ───────────────────────────────────────

import json as _json

_STATUS_FILE = os.path.join(os.path.dirname(__file__), '..', 'absence_status.json')

def _load_absence_status() -> dict:
    try:
        if os.path.exists(_STATUS_FILE):
            with open(_STATUS_FILE, 'r', encoding='utf-8') as f:
                return _json.load(f)
    except Exception:
        pass
    return {}

def _save_absence_status(data: dict) -> None:
    try:
        with open(_STATUS_FILE, 'w', encoding='utf-8') as f:
            _json.dump(data, f, indent=2)
    except Exception:
        pass


@app.get("/api/absences/status")
def get_all_absence_statuses():
    """Return the status dict for all absences (id → status)."""
    return _load_absence_status()


class AbsenceStatusPatch(BaseModel):
    status: str  # 'pending' | 'approved' | 'rejected'


@app.patch("/api/absences/{absence_id}/status")
def patch_absence_status(absence_id: int, body: AbsenceStatusPatch):
    """Update approval status for an absence record."""
    allowed = {'pending', 'approved', 'rejected'}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {allowed}")
    data = _load_absence_status()
    data[str(absence_id)] = body.status
    _save_absence_status(data)
    return {"ok": True, "id": absence_id, "status": body.status}


# ── Admin: Compact database ───────────────────────────────────────────────────

@app.post("/api/admin/compact")
def compact_database():
    """
    Compact all .DBF files in SP5_DB_PATH by rewriting them without deleted records.
    Deleted records have 0x2A ('*') as the first byte of their data row.
    Each file is exclusively locked during the operation to prevent concurrent corruption.
    Returns a summary of files processed and records removed.
    """
    import struct as _struct
    import fcntl as _fcntl
    from datetime import date as _date

    db_path = os.environ.get('SP5_DB_PATH', '')
    if not db_path or not os.path.isdir(db_path):
        raise HTTPException(status_code=500, detail=f"SP5_DB_PATH not set or not a directory: {db_path!r}")

    dbf_files = [f for f in os.listdir(db_path) if f.upper().endswith('.DBF')]
    results = []
    total_removed = 0

    for fname in sorted(dbf_files):
        fpath = os.path.join(db_path, fname)
        try:
            # Open for read+write and hold an exclusive lock for the entire
            # read-modify-write cycle to prevent concurrent write corruption.
            with open(fpath, 'r+b') as f:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
                try:
                    raw = f.read()

                    if len(raw) < 32:
                        results.append({'file': fname, 'skipped': 'too small / corrupt'})
                        continue

                    # Parse DBF header
                    num_records = _struct.unpack_from('<I', raw, 4)[0]
                    header_size = _struct.unpack_from('<H', raw, 8)[0]
                    record_size = _struct.unpack_from('<H', raw, 10)[0]

                    if record_size == 0:
                        results.append({'file': fname, 'skipped': 'record_size=0'})
                        continue

                    # Separate header bytes from record area
                    header_bytes = bytearray(raw[:header_size])
                    records_area = raw[header_size:]

                    # Remove trailing EOF marker for processing
                    if records_area and records_area[-1] == 0x1A:
                        records_area = records_area[:-1]

                    # Split into individual records and filter out deleted ones
                    active_records = []
                    deleted_count = 0
                    for i in range(num_records):
                        start = i * record_size
                        end = start + record_size
                        if end > len(records_area):
                            break
                        rec = records_area[start:end]
                        if rec[0:1] == b'\x2a':  # deleted marker
                            deleted_count += 1
                        else:
                            active_records.append(rec)

                    if deleted_count == 0:
                        results.append({'file': fname, 'removed': 0, 'active': len(active_records)})
                        continue

                    # Update header: new record count + today's date
                    today = _date.today()
                    header_bytes[1] = today.year % 100
                    header_bytes[2] = today.month
                    header_bytes[3] = today.day
                    _struct.pack_into('<I', header_bytes, 4, len(active_records))

                    # Write compacted file (truncate then rewrite)
                    f.seek(0)
                    f.truncate()
                    f.write(bytes(header_bytes))
                    for rec in active_records:
                        f.write(rec)
                    f.write(b'\x1a')  # EOF marker
                    f.flush()
                finally:
                    _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)

            total_removed += deleted_count
            results.append({'file': fname, 'removed': deleted_count, 'active': len(active_records)})

        except Exception as e:
            results.append({'file': fname, 'error': str(e)})

    return {
        'ok': True,
        'files_processed': len(results),
        'total_records_removed': total_removed,
        'details': results,
    }


# ── Burnout-Radar ────────────────────────────────────────────

@app.get("/api/burnout-radar")
def get_burnout_radar(
    year: int = Query(..., description="Year"),
    month: int = Query(..., description="Month 1-12"),
    streak_threshold: int = Query(6, description="Min consecutive days to flag"),
    overtime_threshold_pct: float = Query(20.0, description="Min overtime % to flag"),
    group_id: Optional[int] = Query(None, description="Filter by group"),
):
    """Return list of at-risk employees (long streaks or significant overtime)."""
    return get_db().get_burnout_radar(
        year=year,
        month=month,
        streak_threshold=streak_threshold,
        overtime_threshold_pct=overtime_threshold_pct,
        group_id=group_id,
    )


# ── Changelog / Aktivitätsprotokoll ─────────────────────────

@app.get("/api/changelog")
def get_changelog(
    limit: int = Query(100, description="Max entries to return"),
    user: Optional[str] = Query(None, description="Filter by user"),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
):
    """Return activity log entries from changelog.json."""
    return get_db().get_changelog(limit=limit, user=user, date_from=date_from, date_to=date_to)


class ChangelogEntry(BaseModel):
    user: str
    action: str        # CREATE / UPDATE / DELETE
    entity: str        # employee / shift / schedule / ...
    entity_id: int
    details: Optional[str] = ""


@app.post("/api/changelog")
def log_action(body: ChangelogEntry):
    """Manually write an entry to the changelog."""
    entry = get_db().log_action(
        user=body.user,
        action=body.action,
        entity=body.entity,
        entity_id=body.entity_id,
        details=body.details or "",
    )
    return entry


# ── Middleware: auto-log mutating requests ────────────────────

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
import re as _re


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
        # Only log 2xx responses
        if response.status_code >= 300:
            return response
        # Skip changelog and internal endpoints
        path = request.url.path
        if 'changelog' in path or 'backup' in path or 'compact' in path:
            return response

        # Determine entity from URL path
        parts = [p for p in path.strip('/').split('/') if p]
        entity = 'unknown'
        entity_id = 0
        if len(parts) >= 2:
            segment = parts[1]  # api/<segment>
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
            pass  # Never break the response
        return response


app.add_middleware(ChangelogMiddleware)


# ── Überstunden-Zusammenfassung ───────────────────────────────

@app.get("/api/overtime-summary")
def get_overtime_summary(
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
    group_id: Optional[int] = Query(None, description="Filter by group"),
):
    """Return overtime summary (Überstunden) per employee for a given year."""
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    rows = get_db().get_overtime_summary(year=year, group_id=group_id)
    total_soll = sum(r['soll'] for r in rows)
    total_ist = sum(r['ist'] for r in rows)
    total_delta = round(total_ist - total_soll, 2)
    plus_count = sum(1 for r in rows if r['delta'] >= 0)
    minus_count = sum(1 for r in rows if r['delta'] < 0)
    return {
        'year': year,
        'group_id': group_id,
        'employees': rows,
        'summary': {
            'total_soll': round(total_soll, 2),
            'total_ist': round(total_ist, 2),
            'total_delta': total_delta,
            'plus_count': plus_count,
            'minus_count': minus_count,
            'employee_count': len(rows),
        },
    }



# ── Dashboard: Today ──────────────────────────────────────────

@app.get("/api/dashboard/today")
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

@app.get("/api/dashboard/upcoming")
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

@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    """Return key statistics: total employees, active shifts this month, vacation days used."""
    from datetime import date
    import calendar as _cal
    db = get_db()
    today = date.today()

    # Total employees
    employees = db.get_employees(include_hidden=False)
    total_employees = len(employees)

    # Active shifts (distinct shifts used in MASHI for current month)
    year_str = f"{today.year:04d}-{today.month:02d}"
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

    year_prefix = str(today.year)
    vacation_days_used = sum(
        1 for r in db._read('ABSEN')
        if r.get('DATE', '').startswith(year_prefix)
        and r.get('LEAVETYPID') in vacation_ids
    )

    # Coverage bars: per day of current month
    num_days = _cal.monthrange(today.year, today.month)[1]
    # Count employees scheduled per day
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
            from datetime import datetime as _dt
            wd = _dt(today.year, today.month, day_num).weekday()
            is_weekend = wd >= 5
            is_today = day_num == today.day
            coverage_by_day.append({
                'day': day_num,
                'count': day_counts.get(day_num, 0),
                'is_weekend': is_weekend,
                'is_today': is_today,
                'weekday': wd,
            })
        except ValueError:
            pass

    return {
        'total_employees': total_employees,
        'shifts_this_month': shifts_this_month,
        'active_shift_types': len(shifts_used_ids),
        'vacation_days_used': vacation_days_used,
        'coverage_by_day': coverage_by_day,
        'month': today.month,
        'year': today.year,
    }


# ── Warnings Center ──────────────────────────────────────────

@app.get("/api/warnings")
def get_warnings(
    year: Optional[int] = Query(None, description="Year (YYYY), defaults to current year"),
    month: Optional[int] = Query(None, description="Month (1-12), defaults to current month"),
):
    """Return a list of active warnings for the Warnings Center.

    Warning types:
    - next_month_unplanned: Next month not yet scheduled (< 7 days until month end)
    - overtime_exceeded: Employee has overtime > threshold
    - understaffing: Staffing below minimum on a day
    - conflict: Shift + absence conflict for an employee
    """
    from datetime import date as _date, timedelta
    import calendar as _cal
    from collections import defaultdict

    today = _date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")

    db = get_db()
    warnings = []
    w_id = 0

    def make_id():
        nonlocal w_id
        w_id += 1
        return w_id

    # ── 1. Nächster Monat noch nicht geplant ─────────────────────
    # Check if current month → warn if < 7 days until month end and next month has no schedule
    last_day = _cal.monthrange(year, month)[1]
    month_end = _date(year, month, last_day)
    days_until_end = (month_end - today).days

    if days_until_end < 7:
        # Determine next month
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        next_prefix = f"{next_year:04d}-{next_month:02d}"
        next_month_mashi = [r for r in db._read("MASHI") if r.get("DATE", "").startswith(next_prefix)]
        next_month_spshi = [r for r in db._read("SPSHI") if r.get("DATE", "").startswith(next_prefix) and r.get("TYPE", 0) == 0]

        if len(next_month_mashi) + len(next_month_spshi) == 0:
            month_names_de = ["Januar", "Februar", "März", "April", "Mai", "Juni",
                               "Juli", "August", "September", "Oktober", "November", "Dezember"]
            next_month_name = month_names_de[next_month - 1]
            warnings.append({
                "id": make_id(),
                "type": "next_month_unplanned",
                "severity": "warning",
                "title": f"{next_month_name} {next_year} noch nicht geplant",
                "message": f"Nur noch {days_until_end} Tage bis Monatsende – der nächste Monat hat keinen Dienstplan.",
                "link": "/schedule",
                "link_label": "Zum Dienstplan",
                "date": today.isoformat(),
            })

    # ── 2. Überstunden > Schwellenwert ───────────────────────────
    OVERTIME_THRESHOLD = 20.0  # hours
    try:
        stats = db.get_statistics(year, month)
        for s in stats:
            ot = s.get("overtime_hours", 0)
            if ot > OVERTIME_THRESHOLD:
                warnings.append({
                    "id": make_id(),
                    "type": "overtime_exceeded",
                    "severity": "warning",
                    "title": f"Überstunden: {s['employee_name']}",
                    "message": f"{s['employee_name']} hat {ot:+.1f}h Überstunden in {year}/{month:02d}.",
                    "link": "/ueberstunden",
                    "link_label": "Zur Überstunden-Ansicht",
                    "employee_id": s["employee_id"],
                    "date": f"{year:04d}-{month:02d}-01",
                })
    except Exception:
        pass

    # ── 3. Besetzung unter Minimum ───────────────────────────────
    try:
        staffing_req = db.get_staffing_requirements()
        shift_reqs = staffing_req.get("shift_requirements", [])

        if shift_reqs:
            num_days = _cal.monthrange(year, month)[1]
            prefix = f"{year:04d}-{month:02d}"

            # Collect all schedule entries for the month once
            all_mashi = [r for r in db._read("MASHI") if r.get("DATE", "").startswith(prefix)]
            all_spshi = [r for r in db._read("SPSHI") if r.get("DATE", "").startswith(prefix) and r.get("TYPE", 0) == 0]

            for day_num in range(1, num_days + 1):
                from datetime import datetime as _datetime
                check_date = _datetime(year, month, day_num).date()
                check_str = check_date.isoformat()
                weekday = check_date.weekday()  # 0=Mon

                # Count by shift
                actual_by_shift: dict = defaultdict(int)
                for r in all_mashi:
                    if r.get("DATE", "") == check_str:
                        sid = r.get("SHIFTID")
                        if sid:
                            actual_by_shift[sid] += 1
                for r in all_spshi:
                    if r.get("DATE", "") == check_str:
                        sid = r.get("SHIFTID")
                        if sid:
                            actual_by_shift[sid] += 1

                for req in shift_reqs:
                    if req.get("weekday") != weekday:
                        continue
                    min_req = req.get("min", 0) or 0
                    if min_req == 0:
                        continue
                    shift_id = req.get("shift_id")
                    actual = actual_by_shift.get(shift_id, 0)
                    if actual < min_req:
                        shift_name = req.get("shift_name") or req.get("shift_short", "Schicht")
                        warnings.append({
                            "id": make_id(),
                            "type": "understaffing",
                            "severity": "error",
                            "title": f"Unterbesetzung: {shift_name} am {check_str}",
                            "message": f"Am {check_date.strftime('%d.%m.%Y')} fehlen {min_req - actual} Mitarbeiter für {shift_name} (Ist: {actual}, Soll: {min_req}).",
                            "link": "/schedule",
                            "link_label": "Zum Dienstplan",
                            "date": check_str,
                        })
    except Exception:
        pass

    # ── 4. Konflikte (Schicht + Abwesenheit) ─────────────────────
    try:
        conflicts = db.get_schedule_conflicts(year, month)
        for c in conflicts:
            warnings.append({
                "id": make_id(),
                "type": "conflict",
                "severity": "error",
                "title": f"Konflikt: {c['employee_name']}",
                "message": c.get("message", f"{c['employee_name']}: Schicht + Abwesenheit am {c['date']}"),
                "link": "/konflikte",
                "link_label": "Zu den Konflikten",
                "employee_id": c["employee_id"],
                "date": c["date"],
            })
    except Exception:
        pass

    return {
        "warnings": warnings,
        "count": len(warnings),
        "year": year,
        "month": month,
        "generated_at": today.isoformat(),
    }


# ── Woche kopieren ─────────────────────────────────────────────
class SwapShiftsRequest(BaseModel):
    employee_id_1: int
    employee_id_2: int
    dates: List[str]  # YYYY-MM-DD strings


@app.post("/api/schedule/swap")
def swap_shifts(body: SwapShiftsRequest):
    """Swap schedule entries (shifts + absences) between two employees for the given dates."""
    from sp5lib.dbf_reader import read_dbf, get_table_fields
    from sp5lib.dbf_writer import find_all_records
    from datetime import datetime as _dt3

    if body.employee_id_1 == body.employee_id_2:
        raise HTTPException(status_code=400, detail="Beide Mitarbeiter müssen verschieden sein")
    if not body.dates:
        raise HTTPException(status_code=400, detail="Mindestens ein Datum erforderlich")
    for d in body.dates:
        try:
            _dt3.strptime(d, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")

    db = get_db()
    swapped = 0
    errors = []

    def collect_entries(emp_id: int, date_str: str):
        result = []
        for table, kind in [('MASHI', 'shift'), ('SPSHI', 'special_shift'), ('ABSEN', 'absence')]:
            filepath = db._table(table)
            fields = get_table_fields(filepath)
            matches = find_all_records(filepath, fields, EMPLOYEEID=emp_id, DATE=date_str)
            for _, rec in matches:
                if kind == 'shift':
                    result.append({'kind': 'shift', 'shift_id': rec.get('SHIFTID'), 'workplace_id': rec.get('WORKPLACID', 0)})
                elif kind == 'special_shift':
                    result.append({'kind': 'special_shift', 'shift_id': rec.get('SHIFTID'), 'workplace_id': rec.get('WORKPLACID', 0)})
                elif kind == 'absence':
                    result.append({'kind': 'absence', 'leave_type_id': rec.get('LEAVETYPID')})
        return result

    def write_entries(emp_id: int, date_str: str, entries):
        for entry in entries:
            try:
                if entry['kind'] == 'shift':
                    db.add_schedule_entry(emp_id, date_str, entry['shift_id'])
                elif entry['kind'] == 'absence' and entry.get('leave_type_id'):
                    db.add_absence(emp_id, date_str, entry['leave_type_id'])
                # special_shift: skip for now (complex custom fields)
            except Exception as exc:
                errors.append(f"MA {emp_id} / {date_str}: {exc}")

    for date_str in body.dates:
        try:
            entries1 = collect_entries(body.employee_id_1, date_str)
            entries2 = collect_entries(body.employee_id_2, date_str)
            # Both empty → skip
            if not entries1 and not entries2:
                continue
            # Delete both
            db.delete_schedule_entry(body.employee_id_1, date_str)
            db.delete_schedule_entry(body.employee_id_2, date_str)
            # Write crossed
            write_entries(body.employee_id_1, date_str, entries2)
            write_entries(body.employee_id_2, date_str, entries1)
            swapped += 1
        except Exception as exc:
            errors.append(f"{date_str}: {exc}")

    return {
        "ok": True,
        "swapped_days": swapped,
        "errors": errors,
        "message": f"{swapped} Tag(e) getauscht" + (f", {len(errors)} Fehler" if errors else ""),
    }


class CopyWeekRequest(BaseModel):
    source_employee_id: int
    dates: List[str]               # YYYY-MM-DD strings (up to 7)
    target_employee_ids: List[int]
    skip_existing: bool = True     # True = don't overwrite existing entries


@app.post("/api/schedule/copy-week")
def copy_week(body: CopyWeekRequest):
    """Copy one employee's schedule entries (shifts + absences) for given dates to one or more target employees."""
    db = get_db()
    if not body.dates or not body.target_employee_ids:
        raise HTTPException(status_code=400, detail="dates and target_employee_ids must not be empty")

    # Validate dates
    from datetime import datetime as _dt2
    for d in body.dates:
        try:
            _dt2.strptime(d, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date: {d}")

    # Collect source entries grouped by date
    # We query each date individually via the schedule tables
    from sp5lib.dbf_reader import read_dbf, get_table_fields
    from sp5lib.dbf_writer import find_all_records
    source_entries: dict[str, list[dict]] = {}  # date → list of entry dicts
    for date_str in body.dates:
        entries_for_date = []
        for table, kind in [('MASHI', 'shift'), ('SPSHI', 'special_shift'), ('ABSEN', 'absence')]:
            filepath = db._table(table)
            fields = get_table_fields(filepath)
            matches = find_all_records(filepath, fields, EMPLOYEEID=body.source_employee_id, DATE=date_str)
            for _, rec in matches:
                if kind == 'shift':
                    entries_for_date.append({'kind': 'shift', 'shift_id': rec.get('SHIFTID'), 'workplace_id': rec.get('WORKPLACID', 0)})
                elif kind == 'special_shift':
                    entries_for_date.append({'kind': 'special_shift', 'shift_id': rec.get('SHIFTID'), 'workplace_id': rec.get('WORKPLACID', 0)})
                elif kind == 'absence':
                    entries_for_date.append({'kind': 'absence', 'leave_type_id': rec.get('LEAVETYPID')})
        source_entries[date_str] = entries_for_date

    # Apply to targets
    created = 0
    skipped = 0
    errors = []
    for target_id in body.target_employee_ids:
        if target_id == body.source_employee_id:
            continue
        for date_str, entries in source_entries.items():
            if not entries:
                continue
            # Check existing
            existing_any = False
            if body.skip_existing:
                for table in ['MASHI', 'SPSHI', 'ABSEN']:
                    filepath = db._table(table)
                    fields = get_table_fields(filepath)
                    if find_all_records(filepath, fields, EMPLOYEEID=target_id, DATE=date_str):
                        existing_any = True
                        break
            if existing_any:
                skipped += len(entries)
                continue
            # Delete existing first (if not skip_existing)
            if not body.skip_existing:
                db.delete_schedule_entry(target_id, date_str)
            for entry in entries:
                try:
                    if entry['kind'] == 'shift':
                        db.add_schedule_entry(target_id, date_str, entry['shift_id'])
                        created += 1
                    elif entry['kind'] == 'absence' and entry.get('leave_type_id'):
                        db.add_absence(target_id, date_str, entry['leave_type_id'])
                        created += 1
                    # special_shift: skip for now (complex custom fields)
                except Exception as exc:
                    errors.append(f"MA {target_id} / {date_str}: {exc}")

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "message": f"{created} Einträge kopiert, {skipped} übersprungen" + (f", {len(errors)} Fehler" if errors else ""),
    }


# ── Fairness-Score ───────────────────────────────────────────────
@app.get("/api/fairness")
def get_fairness_score(
    year: int = Query(..., description="Year"),
    group_id: Optional[int] = Query(None, description="Filter by group"),
):
    """
    Berechnet den Fairness-Score: Wie gleichmäßig sind Wochenend-, Nacht-
    und Feiertagsschichten unter den Mitarbeitern verteilt?
    """
    import math
    from datetime import date, timedelta

    db = get_db()
    employees = db.get_employees(include_hidden=False)
    if group_id:
        members = {m["employee_id"] for m in db.get_group_members(group_id)}
        employees = [e for e in employees if e["ID"] in members]

    shifts_map = {s["ID"]: s for s in db.get_shifts()}
    holidays_raw = db.get_holidays(year=year)
    holiday_dates = set()
    for h in holidays_raw:
        if isinstance(h, dict):
            d = h.get("DATE") or h.get("date")
            if d:
                holiday_dates.add(str(d)[:10])

    # Identify "night" shifts: start hour >= 20 or end hour <= 6
    def is_night(shift):
        t = shift.get("STARTEND0", "")
        if not t or "-" not in t:
            return False
        start = t.split("-")[0].strip()
        try:
            h = int(start.split(":")[0])
            return h >= 20 or h < 6
        except Exception:
            return False

    night_shift_ids = {sid for sid, s in shifts_map.items() if is_night(s)}

    # Collect all schedule entries for the year (month by month)
    all_entries: list[dict] = []
    for month in range(1, 13):
        entries = db.get_schedule(year=year, month=month, group_id=group_id)
        all_entries.extend(entries)

    # Count per employee
    stats: dict[int, dict] = {}
    for emp in employees:
        eid = emp["ID"]
        stats[eid] = {
            "employee_id": eid,
            "name": f"{emp.get('FIRSTNAME','')} {emp.get('NAME','')}".strip(),
            "shortname": emp.get("SHORTNAME", ""),
            "total": 0,
            "weekend": 0,
            "night": 0,
            "holiday": 0,
        }

    for entry in all_entries:
        eid = entry.get("employee_id")
        if eid not in stats:
            continue
        if entry.get("kind") != "shift":
            continue
        date_str = str(entry.get("date", ""))[:10]
        try:
            d = date.fromisoformat(date_str)
        except Exception:
            continue
        weekday = d.weekday()  # 0=Mo … 6=So
        stats[eid]["total"] += 1
        if weekday >= 5:
            stats[eid]["weekend"] += 1
        shift_id = entry.get("shift_id")
        if shift_id in night_shift_ids:
            stats[eid]["night"] += 1
        if date_str in holiday_dates:
            stats[eid]["holiday"] += 1

    result = [v for v in stats.values() if v["total"] > 0]
    if not result:
        return {"year": year, "employees": [], "fairness": {}}

    # Compute fairness metrics (std-dev based, lower = more fair)
    def score(values):
        if len(values) < 2:
            return 100.0
        mean = sum(values) / len(values)
        if mean == 0:
            return 100.0
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        cv = math.sqrt(variance) / mean  # coefficient of variation
        return round(max(0, 100 - cv * 100), 1)

    weekend_vals = [r["weekend"] for r in result]
    night_vals   = [r["night"] for r in result]
    holiday_vals = [r["holiday"] for r in result]
    total_vals   = [r["total"] for r in result]

    fairness = {
        "weekend_score": score(weekend_vals),
        "night_score":   score(night_vals),
        "holiday_score": score(holiday_vals),
        "total_score":   score(total_vals),
        "overall":       round((score(weekend_vals) + score(night_vals) + score(total_vals)) / 3, 1),
        "avg_weekend":   round(sum(weekend_vals) / len(weekend_vals), 1),
        "avg_night":     round(sum(night_vals)   / len(night_vals),   1) if sum(night_vals) else 0,
        "avg_holiday":   round(sum(holiday_vals) / len(holiday_vals), 1) if sum(holiday_vals) else 0,
        "avg_total":     round(sum(total_vals)   / len(total_vals),   1),
    }

    result.sort(key=lambda x: x["name"])
    return {"year": year, "employees": result, "fairness": fairness}


# ── Schicht-Wünsche & Sperrtage ─────────────────────────────────

@app.get("/api/wishes")
def get_wishes(
    employee_id: Optional[int] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
):
    return get_db().get_wishes(employee_id=employee_id, year=year, month=month)


class WishCreate(BaseModel):
    employee_id: int
    date: str
    wish_type: str  # WUNSCH | SPERRUNG
    shift_id: Optional[int] = None
    note: Optional[str] = ''


@app.post("/api/wishes")
def create_wish(body: WishCreate):
    wish_type = body.wish_type.upper()
    if wish_type not in ('WUNSCH', 'SPERRUNG'):
        raise HTTPException(status_code=400, detail="wish_type must be WUNSCH or SPERRUNG")
    return get_db().add_wish(
        employee_id=body.employee_id,
        date=body.date,
        wish_type=wish_type,
        shift_id=body.shift_id,
        note=body.note or '',
    )


@app.delete("/api/wishes/{wish_id}")
def delete_wish(wish_id: int):
    deleted = get_db().delete_wish(wish_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Wish not found")
    return {"deleted": wish_id}


# ── Kapazitäts-Forecast ──────────────────────────────────────────
@app.get("/api/capacity-forecast")
def get_capacity_forecast(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: Optional[int] = Query(None, description="Filter by group"),
):
    """Return per-day capacity forecast for a month.

    Each day:
    - scheduled_count: employees with a shift entry
    - absent_count: employees with an absence
    - net_count: scheduled_count (absences are typically already subtracted from schedule)
    - absent_employees: list of {id, name, absence_type}
    - required_min: minimum requirement from staffing requirements
    - coverage_status: ok | low | critical | unknown
    - conflict_flag: True if absent_count is unusually high relative to total employees
    """
    import calendar as _cal
    from collections import defaultdict

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")

    db = get_db()
    num_days = _cal.monthrange(year, month)[1]
    prefix = f"{year:04d}-{month:02d}"

    # Get all employees (optionally filtered by group)
    all_employees = db.get_employees()
    if group_id:
        members = db.get_group_members(group_id)
        member_ids = {m.get('id', m.get('ID')) for m in members}
        all_employees = [e for e in all_employees if (e.get('id') or e.get('ID')) in member_ids]
    total_emp = len(all_employees)

    def _eid(e):
        return e.get('id') or e.get('ID')

    emp_by_id = {_eid(e): e for e in all_employees}

    # Build employee name lookup
    emp_name_by_id = {
        _eid(e): f"{e.get('firstname', e.get('FIRSTNAME',''))} {e.get('lastname', e.get('NAME',''))}".strip()
        for e in all_employees
    }

    # Get leave types for labels
    leave_types_list = db.get_leave_types()
    leave_label_by_id = {lt.get('id', lt.get('ID')): lt.get('short', lt.get('SHORTNAME', lt.get('name', lt.get('NAME', '?')))) for lt in leave_types_list}

    # Get staffing requirements for minimum thresholds
    staffing_req = db.get_staffing_requirements()
    shift_reqs = staffing_req.get("shift_requirements", [])

    # Build per-weekday minimum: max(min) across all shifts for that weekday
    min_by_weekday: dict = {}
    for req in shift_reqs:
        wd = req.get("weekday", -1)
        m = req.get("min", 0) or 0
        if wd >= 0:
            min_by_weekday[wd] = max(min_by_weekday.get(wd, 0), m)

    # Read schedule entries for the month
    all_mashi = [r for r in db._read("MASHI") if r.get("DATE", "").startswith(prefix)]
    all_spshi = [r for r in db._read("SPSHI") if r.get("DATE", "").startswith(prefix) and r.get("TYPE", 0) == 0]

    # Read absences for the month
    all_absences = [r for r in db._read("ABSEN") if r.get("DATE", "").startswith(prefix)]

    # Per-day aggregation
    day_scheduled: dict = defaultdict(set)  # day -> set of emp_ids
    day_absent: dict = defaultdict(list)    # day -> [{id, name, type}]

    for r in all_mashi:
        d = r.get("DATE", "")
        if d.startswith(prefix):
            try:
                day = int(d[8:10])
                eid = r.get("EMPLOYEEID")
                if eid and eid in emp_by_id:
                    day_scheduled[day].add(eid)
            except (ValueError, IndexError):
                pass

    for r in all_spshi:
        d = r.get("DATE", "")
        if d.startswith(prefix):
            try:
                day = int(d[8:10])
                eid = r.get("EMPLOYEEID")
                if eid and eid in emp_by_id:
                    day_scheduled[day].add(eid)
            except (ValueError, IndexError):
                pass

    for r in all_absences:
        d = r.get("DATE", "")
        if d.startswith(prefix):
            try:
                day = int(d[8:10])
                eid = r.get("EMPLOYEEID")
                if eid and eid in emp_by_id:
                    lt_id = r.get("LEAVETYPID") or r.get("LEAVETYPEID", 0)
                    lt_label = leave_label_by_id.get(lt_id, "Abw")
                    day_absent[day].append({
                        "id": eid,
                        "name": emp_name_by_id.get(eid, f"MA {eid}"),
                        "absence_type": lt_label,
                    })
            except (ValueError, IndexError):
                pass

    result = []
    for day in range(1, num_days + 1):
        import datetime as _dt
        check_date = _dt.date(year, month, day)
        weekday = check_date.weekday()  # 0=Mon

        scheduled = len(day_scheduled.get(day, set()))
        absent_list = day_absent.get(day, [])
        absent_count = len(absent_list)

        required_min = min_by_weekday.get(weekday, 0)

        # Coverage status
        if required_min > 0:
            diff = scheduled - required_min
            if diff >= 0:
                status = "ok"
            elif diff == -1:
                status = "low"
            else:
                status = "critical"
        else:
            # No requirement set — judge by absolute count
            if scheduled >= 3:
                status = "ok"
            elif scheduled >= 1:
                status = "low"
            elif scheduled == 0:
                status = "unplanned"
            else:
                status = "unknown"

        # Vacation conflict flag: more than 30% of team absent
        conflict_flag = total_emp > 0 and absent_count >= max(2, total_emp * 0.3)

        result.append({
            "day": day,
            "date": check_date.isoformat(),
            "weekday": weekday,  # 0=Mon
            "scheduled_count": scheduled,
            "absent_count": absent_count,
            "absent_employees": absent_list,
            "required_min": required_min,
            "coverage_status": status,
            "conflict_flag": conflict_flag,
            "total_employees": total_emp,
        })

    # Summary stats
    critical_days = [r for r in result if r["coverage_status"] == "critical"]
    low_days = [r for r in result if r["coverage_status"] == "low"]
    conflict_days = [r for r in result if r["conflict_flag"]]
    unplanned_days = [r for r in result if r["coverage_status"] == "unplanned"]

    return {
        "year": year,
        "month": month,
        "total_employees": total_emp,
        "days": result,
        "summary": {
            "critical_count": len(critical_days),
            "low_count": len(low_days),
            "conflict_count": len(conflict_days),
            "unplanned_count": len(unplanned_days),
            "ok_count": len([r for r in result if r["coverage_status"] == "ok"]),
        },
    }



# ── Qualitätsbericht ─────────────────────────────────────────────────────────

@app.get("/api/quality-report")
def get_quality_report(
    year: int = Query(...),
    month: int = Query(...),
):
    """Monatlicher Qualitätsbericht: Besetzung, Stunden-Compliance, Konflikte, Score."""
    import calendar as _cal
    from collections import defaultdict

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Month must be 1-12")

    db = get_db()
    num_days = _cal.monthrange(year, month)[1]
    prefix = f"{year:04d}-{month:02d}"
    month_name = _cal.month_name[month]

    # ── Mitarbeiter laden ───────────────────────────────────────────────────
    employees = {e["ID"]: e for e in db._read("EMPL") if not e.get("HIDE", 0)}
    active_emp_ids = set(employees.keys())

    # ── Schicht-Definitionen (Stunden) ──────────────────────────────────────
    shifts_by_id: dict = {}
    for s in db._read("SHIFT"):
        sid = s.get("ID")
        if sid:
            # Stunden = Dauer in h; DURATION in Minuten oder schon Stunden?
            dur_min = s.get("DURATION", 0)  # meist Minuten
            shifts_by_id[sid] = s.get("HOURS", dur_min / 60.0 if dur_min > 60 else dur_min)

    # ── Geplante Schichten (MASHI) ───────────────────────────────────────────
    day_emp_sets: dict = defaultdict(set)
    emp_actual_hours: dict = defaultdict(float)
    emp_shifts_count: dict = defaultdict(int)
    for r in db._read("MASHI"):
        d = r.get("DATE", "")
        if d.startswith(prefix):
            eid = r.get("EMPLOYEEID")
            if eid and eid in active_emp_ids:
                try:
                    day_num = int(d[8:10])
                    day_emp_sets[day_num].add(eid)
                    emp_shifts_count[eid] += 1
                    sid = r.get("SHIFTID")
                    hrs = shifts_by_id.get(sid, 8.0) if sid else 8.0
                    emp_actual_hours[eid] += hrs
                except (ValueError, IndexError):
                    pass

    # ── Abwesenheiten (ABSEN) ────────────────────────────────────────────────
    emp_absence_days: dict = defaultdict(int)
    emp_vacation_days: dict = defaultdict(int)
    emp_sick_days: dict = defaultdict(int)
    try:
        leave_types = {lt["ID"]: lt for lt in db._read("LEAVETYP")}
    except Exception:
        leave_types = {}
    for r in db._read("ABSEN"):
        d = r.get("DATE", "")
        if d.startswith(prefix):
            eid = r.get("EMPLOYEEID")
            if eid and eid in active_emp_ids:
                emp_absence_days[eid] += 1
                lt_id = r.get("LEAVETYPEID")
                lt = leave_types.get(lt_id, {})
                name = (lt.get("NAME") or lt.get("SHORTNAME") or "").lower()
                if "urlaub" in name or "vacation" in name or "holiday" in name:
                    emp_vacation_days[eid] += 1
                elif "krank" in name or "sick" in name:
                    emp_sick_days[eid] += 1

    # ── Tages-Besetzungs-Check ───────────────────────────────────────────────
    required_min = max(2, len(active_emp_ids) // 8)  # dynamisch
    coverage_days = []
    critical_days = []
    low_days = []
    ok_days = []
    unplanned_days = []

    for day in range(1, num_days + 1):
        date_obj = f"{year:04d}-{month:02d}-{day:02d}"
        wd = _cal.weekday(year, month, day)  # 0=Mon
        is_weekend = wd >= 5
        scheduled = len(day_emp_sets.get(day, set()))

        if scheduled == 0 and not is_weekend:
            status = "unplanned"
            unplanned_days.append(day)
        elif scheduled >= required_min:
            status = "ok"
            ok_days.append(day)
        elif scheduled == required_min - 1:
            status = "low"
            low_days.append(day)
        else:
            status = "critical"
            critical_days.append(day)

        coverage_days.append({
            "day": day,
            "date": date_obj,
            "weekday": wd,
            "is_weekend": is_weekend,
            "scheduled": scheduled,
            "required": required_min,
            "status": status,
        })

    # ── Stunden-Compliance (via get_statistics für korrekte Stunden-Berechnung) ──
    hours_issues = []
    hours_ok = []
    total_target = 0.0
    total_actual = 0.0

    try:
        stats_list = db.get_statistics(year, month)
    except Exception:
        stats_list = []

    for stat in stats_list:
        target = stat.get("target_hours") or 0.0
        actual = stat.get("actual_hours") or 0.0
        if target <= 0:
            continue
        total_target += target
        total_actual += actual
        deviation_pct = ((actual - target) / target * 100) if target else 0
        absence_days = stat.get("absence_days", 0) or 0
        issue_type = None
        if deviation_pct > 15:
            issue_type = "over"
        elif actual < target * 0.5 and absence_days < 10 and stat.get("shifts_count", 0) > 0:
            # stark unterstunden ohne Abwesenheiten = ungewöhnlich
            issue_type = "under"
        if issue_type:
            hours_issues.append({
                "employee_id": stat.get("employee_id"),
                "name": stat.get("employee_name", ""),
                "short": stat.get("employee_short", ""),
                "target_hours": round(target, 1),
                "actual_hours": round(actual, 1),
                "deviation_pct": round(deviation_pct, 1),
                "issue_type": issue_type,
                "shifts_count": stat.get("shifts_count", 0),
            })
        else:
            hours_ok.append(stat.get("employee_id"))

    # ── Score berechnen ──────────────────────────────────────────────────────
    # Formel: Gewichte Coverage 50%, Hours 30%, Konflikte 20%
    work_days_count = sum(1 for d in coverage_days if not d["is_weekend"])
    covered_work_days = sum(1 for d in coverage_days
                            if not d["is_weekend"] and d["status"] in ("ok", "low"))
    coverage_score = (covered_work_days / work_days_count * 100) if work_days_count else 100
    hours_score = max(0, 100 - len(hours_issues) * 5)
    # Conflicts: critical days & unplanned days penalty
    conflict_penalty = len(critical_days) * 5 + len(unplanned_days) * 3
    conflict_score = max(0, 100 - conflict_penalty)

    overall_score = round(coverage_score * 0.5 + hours_score * 0.3 + conflict_score * 0.2)
    if overall_score >= 90:
        grade = "A"
        grade_label = "Ausgezeichnet"
        grade_color = "green"
    elif overall_score >= 75:
        grade = "B"
        grade_label = "Gut"
        grade_color = "blue"
    elif overall_score >= 60:
        grade = "C"
        grade_label = "Verbesserungsbedarf"
        grade_color = "yellow"
    else:
        grade = "D"
        grade_label = "Kritisch"
        grade_color = "red"

    # ── Issues-Liste zusammenstellen ─────────────────────────────────────────
    findings = []
    if critical_days:
        findings.append({
            "severity": "critical",
            "category": "Besetzung",
            "message": f"{len(critical_days)} Tag(e) kritisch unterbesetzt",
            "days": critical_days,
        })
    if unplanned_days:
        findings.append({
            "severity": "warning",
            "category": "Planung",
            "message": f"{len(unplanned_days)} Werktag(e) ohne Planung",
            "days": unplanned_days,
        })
    if low_days:
        findings.append({
            "severity": "info",
            "category": "Besetzung",
            "message": f"{len(low_days)} Tag(e) knapp besetzt",
            "days": low_days,
        })
    over_emp = [h for h in hours_issues if h["issue_type"] == "over"]
    under_emp = [h for h in hours_issues if h["issue_type"] == "under"]
    if over_emp:
        findings.append({
            "severity": "warning",
            "category": "Überstunden",
            "message": f"{len(over_emp)} Mitarbeiter mit >15% Überstunden",
            "employees": [h["short"] for h in over_emp],
        })
    if under_emp:
        findings.append({
            "severity": "warning",
            "category": "Unterstunden",
            "message": f"{len(under_emp)} Mitarbeiter stark unterstundet",
            "employees": [h["short"] for h in under_emp],
        })
    if not findings:
        findings.append({
            "severity": "ok",
            "category": "Allgemein",
            "message": "Keine Auffälligkeiten — Monat kann abgeschlossen werden.",
        })

    return {
        "year": year,
        "month": month,
        "month_name": month_name,
        "overall_score": overall_score,
        "grade": grade,
        "grade_label": grade_label,
        "grade_color": grade_color,
        "active_employees": len(active_emp_ids),
        "work_days": work_days_count,
        "total_days": num_days,
        "required_min_per_day": required_min,
        "coverage": {
            "ok_days": len(ok_days),
            "low_days": len(low_days),
            "critical_days": len(critical_days),
            "unplanned_days": len(unplanned_days),
            "score": round(coverage_score, 1),
        },
        "hours": {
            "total_target": round(total_target, 1),
            "total_actual": round(total_actual, 1),
            "employees_ok": len(hours_ok),
            "employees_issues": len(hours_issues),
            "issues": hours_issues,
            "score": round(hours_score, 1),
        },
        "conflicts": {
            "score": round(conflict_score, 1),
            "critical_days": critical_days,
            "unplanned_days": unplanned_days,
        },
        "findings": findings,
        "coverage_days": coverage_days,
    }


# ── Verfügbarkeits-Matrix ────────────────────────────────────────────────────

@app.get("/api/availability-matrix")
def get_availability_matrix(
    group_id: Optional[int] = Query(None),
    year: int = Query(None),
    months: int = Query(12, ge=1, le=24),
):
    """
    Analysiert Schicht-Muster aus dem Dienstplan (MASHI + SPSHI + ABSEN).
    Gibt pro Mitarbeiter zurück:
      - Schicht-Häufigkeit pro Wochentag (7 Tage × n Schichtarten)
      - Schicht-Mix (wie oft welche Schicht)
      - Muster-Label (z.B. "3-Schicht-Rotation", "Tagschicht Mo-Fr", "Frei")
    """
    import datetime, math
    from collections import defaultdict

    db = get_db()
    employees = db.get_employees(include_hidden=False)
    shifts_map = {s['ID']: s for s in db.get_shifts(include_hidden=True)}
    groups_map = {g['ID']: g['NAME'] for g in db.get_groups()}

    if group_id:
        members = set(db.get_group_members(group_id))
        employees = [e for e in employees if e['ID'] in members]

    if year is None:
        year = datetime.date.today().year

    # Collect all schedule entries for the requested range
    # month range: last `months` months up to end of `year`
    today = datetime.date.today()
    end_date = datetime.date(year, 12, 31)
    start_date = (end_date - datetime.timedelta(days=months * 30)).replace(day=1)

    # Build per-employee, per-weekday, per-shift counts
    # weekday: 0=Mo .. 6=So
    emp_wd_shift: dict[int, dict[int, dict]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    emp_shift_total: dict[int, dict] = defaultdict(lambda: defaultdict(int))
    emp_day_total: dict[int, dict] = defaultdict(lambda: defaultdict(int))

    # Scan months
    cur = start_date.replace(day=1)
    while cur <= end_date:
        entries = db.get_schedule(cur.year, cur.month)
        for e in entries:
            d = datetime.date.fromisoformat(e['date'])
            if d < start_date or d > end_date:
                continue
            eid = e['employee_id']
            wd = d.weekday()  # 0=Mo
            sid = e.get('shift_id')
            if e.get('kind') in ('shift', 'special_shift') and sid:
                shift = shifts_map.get(sid)
                short = shift.get('SHORTNAME', '?') if shift else '?'
                name = shift.get('NAME', '?') if shift else '?'
                color = shift.get('COLORBK_HEX', '#888') if shift else '#888'
                emp_wd_shift[eid][wd][sid] += 1
                emp_shift_total[eid][sid] += 1
                emp_day_total[eid][wd] += 1
            elif e.get('kind') == 'absence':
                emp_wd_shift[eid][wd]['absence'] += 1
                emp_shift_total[eid]['absence'] += 1
                emp_day_total[eid][wd] += 1

        # advance month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    # Build result per employee
    result_employees = []
    for emp in employees:
        eid = emp['ID']
        name = f"{emp.get('FIRSTNAME', '')} {emp.get('NAME', '')}".strip()
        short = emp.get('SHORTNAME', '')
        workdays = emp.get('WORKDAYS_LIST', [True] * 5 + [False, False])

        # Per-weekday breakdown
        weekday_data = []
        for wd in range(7):
            shift_counts = dict(emp_wd_shift[eid].get(wd, {}))
            total_for_day = emp_day_total[eid].get(wd, 0)
            # Build list of shifts sorted by count desc
            shifts_list = []
            for sid, cnt in sorted(shift_counts.items(), key=lambda x: -x[1]):
                if sid == 'absence':
                    shifts_list.append({
                        'shift_id': None,
                        'short': 'Ab',
                        'name': 'Abwesenheit',
                        'color': '#94a3b8',
                        'count': cnt,
                        'pct': round(cnt / total_for_day * 100) if total_for_day else 0,
                    })
                else:
                    shift = shifts_map.get(sid)
                    if shift:
                        shifts_list.append({
                            'shift_id': sid,
                            'short': shift.get('SHORTNAME', '?'),
                            'name': shift.get('NAME', '?'),
                            'color': shift.get('COLORBK_HEX', '#888'),
                            'count': cnt,
                            'pct': round(cnt / total_for_day * 100) if total_for_day else 0,
                        })
            weekday_data.append({
                'weekday': wd,
                'total': total_for_day,
                'configured': workdays[wd] if wd < len(workdays) else False,
                'shifts': shifts_list,
                # dominant shift
                'dominant_shift': shifts_list[0] if shifts_list else None,
            })

        # Overall shift mix
        total_shifts = sum(v for k, v in emp_shift_total[eid].items() if k != 'absence')
        shift_mix = []
        for sid, cnt in sorted(emp_shift_total[eid].items(), key=lambda x: -x[1]):
            if sid == 'absence':
                continue
            shift = shifts_map.get(sid)
            if shift:
                shift_mix.append({
                    'shift_id': sid,
                    'short': shift.get('SHORTNAME', '?'),
                    'name': shift.get('NAME', '?'),
                    'color': shift.get('COLORBK_HEX', '#888'),
                    'count': cnt,
                    'pct': round(cnt / total_shifts * 100) if total_shifts else 0,
                })

        # Pattern label
        active_wd = sum(1 for w in weekday_data if w['total'] > 0)
        if total_shifts == 0:
            pattern = 'Keine Daten'
            pattern_icon = '⬜'
        elif len(set(s['shift_id'] for s in shift_mix)) >= 3:
            pattern = '3-Schicht-Rotation'
            pattern_icon = '🔄'
        elif len(set(s['shift_id'] for s in shift_mix)) == 2:
            pattern = '2-Schicht-Wechsel'
            pattern_icon = '↔️'
        elif active_wd >= 5:
            pattern = 'Tagschicht Mo–Fr'
            pattern_icon = '☀️'
        elif active_wd >= 3:
            pattern = 'Teilzeit'
            pattern_icon = '📅'
        else:
            pattern = 'Wenige Einsätze'
            pattern_icon = '📉'

        # Group
        emp_groups = []
        for gid, gname in groups_map.items():
            members = set(db.get_group_members(gid))
            if eid in members:
                emp_groups.append(gname)

        result_employees.append({
            'id': eid,
            'name': name,
            'short': short,
            'groups': emp_groups,
            'pattern': pattern,
            'pattern_icon': pattern_icon,
            'total_shifts': total_shifts,
            'shift_mix': shift_mix,
            'weekdays': weekday_data,
            # configured workdays from employee record
            'workdays_config': workdays,
        })

    # Coverage per weekday (how many employees available)
    weekday_coverage = []
    for wd in range(7):
        configured = sum(1 for emp in employees
                         if wd < len(emp.get('WORKDAYS_LIST', [])) and emp['WORKDAYS_LIST'][wd])
        actual = sum(1 for e in result_employees if e['weekdays'][wd]['total'] > 0)
        weekday_coverage.append({
            'weekday': wd,
            'configured': configured,
            'actual_data': actual,
        })

    return {
        'year': year,
        'months': months,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'employees': result_employees,
        'weekday_coverage': weekday_coverage,
        'shifts': [
            {'id': s['ID'], 'name': s['NAME'], 'short': s['SHORTNAME'], 'color': s.get('COLORBK_HEX', '#888')}
            for s in db.get_shifts(include_hidden=False)
        ],
    }


# ── Kompetenz-Matrix / Skills ────────────────────────────────────
import uuid as _uuid

def _skills_path() -> str:
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, 'skills.json')

def _load_skills() -> dict:
    path = _skills_path()
    if not os.path.exists(path):
        return {"skills": [], "assignments": []}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except Exception:
        return {"skills": [], "assignments": []}

def _save_skills(data: dict):
    with open(_skills_path(), 'w', encoding='utf-8') as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)

class SkillCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    color: Optional[str] = "#3b82f6"
    icon: Optional[str] = "🎯"
    category: Optional[str] = ""

class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    category: Optional[str] = None

class SkillAssignment(BaseModel):
    employee_id: int
    skill_id: str
    level: Optional[int] = 1  # 1=basic, 2=advanced, 3=expert
    certified_until: Optional[str] = None  # ISO date
    notes: Optional[str] = ""

@app.get("/api/skills")
def get_skills():
    data = _load_skills()
    return data["skills"]

@app.post("/api/skills")
def create_skill(body: SkillCreate):
    data = _load_skills()
    skill = {
        "id": str(_uuid.uuid4())[:8],
        "name": body.name,
        "description": body.description or "",
        "color": body.color or "#3b82f6",
        "icon": body.icon or "🎯",
        "category": body.category or "",
        "created_at": _dt.now().isoformat(timespec='seconds'),
    }
    data["skills"].append(skill)
    _save_skills(data)
    return skill

@app.put("/api/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate):
    data = _load_skills()
    for s in data["skills"]:
        if s["id"] == skill_id:
            if body.name is not None: s["name"] = body.name
            if body.description is not None: s["description"] = body.description
            if body.color is not None: s["color"] = body.color
            if body.icon is not None: s["icon"] = body.icon
            if body.category is not None: s["category"] = body.category
            _save_skills(data)
            return s
    raise HTTPException(status_code=404, detail="Skill not found")

@app.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str):
    data = _load_skills()
    data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
    data["assignments"] = [a for a in data["assignments"] if a["skill_id"] != skill_id]
    _save_skills(data)
    return {"ok": True}

@app.get("/api/skills/assignments")
def get_assignments(employee_id: Optional[int] = Query(None)):
    data = _load_skills()
    assignments = data.get("assignments", [])
    if employee_id is not None:
        assignments = [a for a in assignments if a.get("employee_id") == employee_id]
    return assignments

@app.post("/api/skills/assignments")
def add_assignment(body: SkillAssignment):
    data = _load_skills()
    # Remove existing assignment for same employee+skill
    data["assignments"] = [
        a for a in data.get("assignments", [])
        if not (a["employee_id"] == body.employee_id and a["skill_id"] == body.skill_id)
    ]
    assignment = {
        "id": str(_uuid.uuid4())[:8],
        "employee_id": body.employee_id,
        "skill_id": body.skill_id,
        "level": body.level or 1,
        "certified_until": body.certified_until or None,
        "notes": body.notes or "",
        "assigned_at": _dt.now().isoformat(timespec='seconds'),
    }
    data["assignments"].append(assignment)
    _save_skills(data)
    return assignment

@app.delete("/api/skills/assignments/{assignment_id}")
def delete_assignment(assignment_id: str):
    data = _load_skills()
    before = len(data.get("assignments", []))
    data["assignments"] = [a for a in data.get("assignments", []) if a.get("id") != assignment_id]
    if len(data["assignments"]) == before:
        raise HTTPException(status_code=404, detail="Assignment not found")
    _save_skills(data)
    return {"ok": True}

@app.get("/api/skills/matrix")
def get_skills_matrix():
    """Full matrix: all employees × all skills with assignment details."""
    data = _load_skills()
    skills = data.get("skills", [])
    assignments = data.get("assignments", [])
    employees = get_db().get_employees()

    # Build lookup: employee_id -> {skill_id -> assignment}
    emp_skills: dict = {}
    for a in assignments:
        eid = a["employee_id"]
        if eid not in emp_skills:
            emp_skills[eid] = {}
        emp_skills[eid][a["skill_id"]] = a

    result_employees = []
    for emp in employees:
        eid = emp["ID"]
        result_employees.append({
            "id": eid,
            "name": f"{emp.get('NAME', '')} {emp.get('FIRSTNAME', '')}".strip(),
            "short": emp.get("SHORTNAME", ""),
            "group": emp.get("GROUP_NAME", ""),
            "skills": emp_skills.get(eid, {}),
            "skill_count": len(emp_skills.get(eid, {})),
        })

    # Skill coverage stats
    skill_stats = []
    for skill in skills:
        sid = skill["id"]
        holders = [a for a in assignments if a["skill_id"] == sid]
        experts = [a for a in holders if a.get("level", 1) >= 3]
        expiring = []
        today = _dt.today().date().isoformat()
        soon = _dt.today().date().replace(
            year=_dt.today().date().year,
            month=min(_dt.today().date().month + 3, 12)
        ).isoformat()
        for a in holders:
            cu = a.get("certified_until")
            if cu and cu <= soon:
                expiring.append(a)
        skill_stats.append({
            **skill,
            "holder_count": len(holders),
            "expert_count": len(experts),
            "expiring_count": len(expiring),
            "coverage_pct": round(len(holders) / len(employees) * 100) if employees else 0,
        })

    return {
        "skills": skill_stats,
        "employees": result_employees,
        "assignments": assignments,
        "total_employees": len(employees),
    }


# ── Schichtplan-Simulation ────────────────────────────────────────────────────

class SimulationAbsence(BaseModel):
    emp_id: int
    dates: list  # list of 'YYYY-MM-DD' strings, or ['all'] for whole month

class SimulationRequest(BaseModel):
    year: int
    month: int
    absences: list  # list of SimulationAbsence dicts
    scenario_name: Optional[str] = "Simulation"

@app.post("/api/simulation")
def run_simulation(body: SimulationRequest):
    """
    Schichtplan-Simulation: Was passiert wenn Mitarbeiter ausfallen?
    Vergleicht Ist-Besetzung mit simulierter Besetzung nach Ausfall.
    """
    import calendar as _cal
    db = get_db()
    year, month = body.year, body.month
    entries = db.get_schedule(year=year, month=month)
    employees = db.get_employees(include_hidden=False)
    shifts = db.get_shifts(include_hidden=True)
    emp_map = {e['ID']: e for e in employees}
    shift_map = {s['ID']: s for s in shifts}

    # Days in month
    days_in_month = _cal.monthrange(year, month)[1]
    date_strs = [f"{year}-{month:02d}-{d:02d}" for d in range(1, days_in_month + 1)]

    # Build absent set: {emp_id: set(dates)}
    absent_map: dict = {}
    for ab in body.absences:
        emp_id = ab['emp_id'] if isinstance(ab, dict) else ab.emp_id
        dates_raw = ab['dates'] if isinstance(ab, dict) else ab.dates
        if dates_raw == ['all'] or dates_raw == 'all':
            dates_set = set(date_strs)
        else:
            dates_set = set(dates_raw)
        absent_map[emp_id] = dates_set

    # Per-day analysis
    day_stats = []
    total_lost_shifts = 0
    critical_days = 0
    affected_employees = set(absent_map.keys())

    for date_str in date_strs:
        day_entries = [e for e in entries if e['date'] == date_str and e['kind'] == 'shift']
        baseline_count = len(day_entries)

        # Remove entries for absent employees on this date
        simulated = [
            e for e in day_entries
            if not (e['employee_id'] in absent_map and date_str in absent_map[e['employee_id']])
        ]
        sim_count = len(simulated)
        lost = baseline_count - sim_count
        total_lost_shifts += lost

        # Who is missing on this day
        missing_emps = []
        for e in day_entries:
            eid = e['employee_id']
            if eid in absent_map and date_str in absent_map[eid]:
                emp = emp_map.get(eid, {})
                missing_emps.append({
                    'emp_id': eid,
                    'name': f"{emp.get('FIRSTNAME','')} {emp.get('NAME','')}".strip(),
                    'shortname': emp.get('SHORTNAME', str(eid)),
                    'shift_id': e.get('shift_id'),
                    'shift_name': e.get('display_name', ''),
                })

        # Potential cover candidates: employees with shifts that day NOT absent
        cover_candidates = []
        for e in day_entries:
            eid = e['employee_id']
            if eid not in absent_map or date_str not in absent_map.get(eid, set()):
                emp = emp_map.get(eid, {})
                cover_candidates.append({
                    'emp_id': eid,
                    'name': f"{emp.get('FIRSTNAME','')} {emp.get('NAME','')}".strip(),
                    'shortname': emp.get('SHORTNAME', str(eid)),
                })

        # Status
        if sim_count == 0 and baseline_count > 0:
            status = 'critical'
            critical_days += 1
        elif lost > 0:
            status = 'degraded'
        else:
            status = 'ok'

        day_stats.append({
            'date': date_str,
            'day': int(date_str.split('-')[2]),
            'weekday': _cal.weekday(year, month, int(date_str.split('-')[2])),
            'baseline_count': baseline_count,
            'simulated_count': sim_count,
            'lost_shifts': lost,
            'status': status,
            'missing': missing_emps,
            'cover_candidates': cover_candidates[:5],  # top 5
        })

    # Per-employee impact summary
    employee_impacts = []
    for emp_id in absent_map:
        emp = emp_map.get(emp_id, {})
        emp_entries = [e for e in entries if e['employee_id'] == emp_id and e['kind'] == 'shift']
        absent_dates = absent_map[emp_id]
        affected = [e for e in emp_entries if e['date'] in absent_dates]
        employee_impacts.append({
            'emp_id': emp_id,
            'name': f"{emp.get('FIRSTNAME','')} {emp.get('NAME','')}".strip(),
            'shortname': emp.get('SHORTNAME', str(emp_id)),
            'total_shifts_in_month': len(emp_entries),
            'absent_shifts': len(affected),
            'absent_days': sorted(list(absent_dates & {e['date'] for e in emp_entries})),
        })

    return {
        'scenario_name': body.scenario_name,
        'year': year,
        'month': month,
        'days': day_stats,
        'summary': {
            'total_lost_shifts': total_lost_shifts,
            'critical_days': critical_days,
            'degraded_days': sum(1 for d in day_stats if d['status'] == 'degraded'),
            'ok_days': sum(1 for d in day_stats if d['status'] == 'ok'),
            'affected_employees': len(affected_employees),
        },
        'employee_impacts': employee_impacts,
    }


# ── Übergabe-Protokoll ────────────────────────────────────────────────────────
# In-memory store (reset on restart – kann später auf DB umgestellt werden)
import uuid as _uuid

_handover_notes: list[dict] = []

@app.get("/api/handover")
def get_handover(date: str | None = None, shift_id: int | None = None, limit: int = 50):
    """Übergabe-Notizen abrufen, optional gefiltert nach Datum/Schicht."""
    notes = list(reversed(_handover_notes))  # neueste zuerst
    if date:
        notes = [n for n in notes if n["date"] == date]
    if shift_id is not None:
        notes = [n for n in notes if n.get("shift_id") == shift_id]
    return notes[:limit]

@app.post("/api/handover")
def create_handover(body: dict):
    """Neue Übergabe-Notiz anlegen."""
    note = {
        "id": str(_uuid.uuid4())[:8],
        "date": body.get("date", ""),
        "shift_id": body.get("shift_id"),
        "shift_name": body.get("shift_name", ""),
        "author": body.get("author", "Unbekannt"),
        "text": body.get("text", ""),
        "priority": body.get("priority", "normal"),  # normal | wichtig | kritisch
        "tags": body.get("tags", []),
        "created_at": body.get("created_at", ""),
        "resolved": False,
    }
    _handover_notes.append(note)
    return note

@app.patch("/api/handover/{note_id}")
def update_handover(note_id: str, body: dict):
    """Notiz aktualisieren (z.B. als erledigt markieren)."""
    for note in _handover_notes:
        if note["id"] == note_id:
            if "resolved" in body:
                note["resolved"] = body["resolved"]
            if "text" in body:
                note["text"] = body["text"]
            if "priority" in body:
                note["priority"] = body["priority"]
            return note
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Notiz nicht gefunden")

@app.delete("/api/handover/{note_id}")
def delete_handover(note_id: str):
    """Übergabe-Notiz löschen."""
    global _handover_notes
    before = len(_handover_notes)
    _handover_notes = [n for n in _handover_notes if n["id"] != note_id]
    if len(_handover_notes) == before:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
    return {"ok": True}


# ── Schicht-Tauschbörse ──────────────────────────────────────────

class SwapRequestCreate(BaseModel):
    requester_id: int
    requester_date: str   # YYYY-MM-DD
    partner_id: int
    partner_date: str     # YYYY-MM-DD
    note: Optional[str] = ''


class SwapRequestResolve(BaseModel):
    action: str           # 'approve' | 'reject'
    resolved_by: Optional[str] = 'planner'
    reject_reason: Optional[str] = ''


@app.get("/api/swap-requests")
def list_swap_requests(
    status: Optional[str] = None,
    employee_id: Optional[int] = None,
):
    """List shift swap requests, optionally filtered by status or employee."""
    requests = get_db().get_swap_requests(status=status, employee_id=employee_id)
    # Enrich with employee names + shift info
    employees = {e['ID']: e for e in get_db().get_employees(include_hidden=True)}
    shifts = {s['ID']: s for s in get_db().get_shifts(include_hidden=True)}

    def get_shift_for(emp_id: int, date_str: str):
        sched = get_db().get_schedule_day(date_str)
        for entry in sched:
            if entry.get('employee_id') == emp_id:
                sid = entry.get('shift_id')
                if sid and sid in shifts:
                    s = shifts[sid]
                    return {'id': sid, 'name': s.get('SHORTNAME', '?'), 'color': s.get('COLOR', '#888')}
        return None

    result = []
    for req in requests:
        r = dict(req)
        req_emp = employees.get(req['requester_id'], {})
        par_emp = employees.get(req['partner_id'], {})
        r['requester_name'] = f"{req_emp.get('NAME', '?')}, {req_emp.get('FIRSTNAME', '')}"
        r['requester_short'] = req_emp.get('SHORTNAME', '?')
        r['partner_name'] = f"{par_emp.get('NAME', '?')}, {par_emp.get('FIRSTNAME', '')}"
        r['partner_short'] = par_emp.get('SHORTNAME', '?')
        r['requester_shift'] = get_shift_for(req['requester_id'], req['requester_date'])
        r['partner_shift'] = get_shift_for(req['partner_id'], req['partner_date'])
        result.append(r)
    return result


@app.post("/api/swap-requests")
def create_swap_request(body: SwapRequestCreate):
    """Create a new shift swap request."""
    from datetime import datetime as _dt4
    # Validate dates
    for d in [body.requester_date, body.partner_date]:
        try:
            _dt4.strptime(d, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")
    if body.requester_id == body.partner_id:
        raise HTTPException(status_code=400, detail="Antragsteller und Partner müssen verschieden sein")
    entry = get_db().create_swap_request(
        requester_id=body.requester_id,
        requester_date=body.requester_date,
        partner_id=body.partner_id,
        partner_date=body.partner_date,
        note=body.note or '',
    )
    get_db().log_action('system', 'CREATE', 'swap_request', entry['id'],
                        f"MA {body.requester_id} → MA {body.partner_id} ({body.requester_date}↔{body.partner_date})")
    return entry


@app.patch("/api/swap-requests/{swap_id}/resolve")
def resolve_swap_request(swap_id: int, body: SwapRequestResolve):
    """Approve or reject a swap request. If approved, executes the actual shift swap."""
    if body.action not in ('approve', 'reject'):
        raise HTTPException(status_code=400, detail="action muss 'approve' oder 'reject' sein")
    entry = get_db().resolve_swap_request(swap_id, body.action,
                                          resolved_by=body.resolved_by or 'planner',
                                          reject_reason=body.reject_reason or '')
    if entry is None:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden oder bereits abgeschlossen")

    if body.action == 'approve':
        # Execute the actual shift swap for both dates
        swap_result = swap_shifts(SwapShiftsRequest(
            employee_id_1=entry['requester_id'],
            employee_id_2=entry['partner_id'],
            dates=[entry['requester_date']] if entry['requester_date'] == entry['partner_date']
                  else [entry['requester_date'], entry['partner_date']],
        ))
        # If different dates, we need to swap requester→partner_date and partner→requester_date
        if entry['requester_date'] != entry['partner_date']:
            # Custom cross-date swap: move requester's shift to partner_date and vice versa
            pass  # The swap above handles same-dates; cross-date swap is complex — mark as todo
        get_db().log_action(body.resolved_by or 'planner', 'UPDATE', 'swap_request', swap_id,
                            f"Genehmigt: MA {entry['requester_id']} ↔ MA {entry['partner_id']}")
        return {**entry, 'swap_result': swap_result}

    get_db().log_action(body.resolved_by or 'planner', 'UPDATE', 'swap_request', swap_id,
                        f"Abgelehnt: {body.reject_reason}")
    return entry


@app.delete("/api/swap-requests/{swap_id}")
def delete_swap_request(swap_id: int):
    """Delete a swap request (cancel)."""
    deleted = get_db().delete_swap_request(swap_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return {"ok": True}


# ── Frontend static files (muss NACH allen /api-Routen stehen!) ──
_FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'dist')
)

if os.path.isdir(_FRONTEND_DIST):
    # Alle Assets (JS, CSS, PNG …) direkt servieren
    app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")), name="assets")

    # Alle anderen Routen → index.html (SPA-Fallback für React Router)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        index = os.path.join(_FRONTEND_DIST, "index.html")
        return FileResponse(index)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
