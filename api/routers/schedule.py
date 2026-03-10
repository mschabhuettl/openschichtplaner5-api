"""Schedule, shift-cycles, staffing, einsatzplan, restrictions router."""


from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..dependencies import (
    _sanitize_500,
    get_db,
    limiter,
    require_admin,
    require_planer,
)
from .events import broadcast

router = APIRouter()


@router.get(
    "/api/schedule",
    tags=["Schedule"],
    summary="Get monthly schedule",
    description="Return the full schedule grid for a given year/month, optionally filtered by group.",
)
def get_schedule(
    year: int = Query(..., description="Year"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: int | None = Query(None, description="Filter by group ID"),
):
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen"
        )
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen",
        )
    return get_db().get_schedule(year=year, month=month, group_id=group_id)


@router.get("/api/cycles", tags=["Schedule"], summary="List schedule cycles", description="Return all configured schedule cycles.")
def get_cycles():
    return get_db().get_cycles()


# ── Staffing requirements ────────────────────────────────────
@router.get("/api/staffing", tags=["Schedule"], summary="Staffing overview", description="Return staffing levels (actual vs required) for a given month.")
def get_staffing(
    year: int = Query(...),
    month: int = Query(...),
):
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen"
        )
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen",
        )
    return get_db().get_staffing(year, month)


# ── Schedule Coverage (Personalbedarf-Ampel) ─────────────────
@router.get(
    "/api/schedule/coverage", tags=["Schedule"], summary="Schedule coverage analysis",
    description="Return daily coverage analysis (ok/low/critical) for a given month.",
)
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
        raise HTTPException(
            status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen"
        )
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen",
        )

    db = get_db()
    num_days = _cal.monthrange(year, month)[1]
    prefix = f"{year:04d}-{month:02d}"

    # Try DADEM / SHDEM for required staff — both empty in most DBs
    # Use per-day required count (default: 3 = "ok" threshold, 2 = "low")
    required_count = 3  # "ok" if scheduled >= 3, "low" if == 2, "critical" if < 2

    # Count distinct employees scheduled per day (MASHI = regular shifts)
    day_emp_sets: dict = defaultdict(set)
    for r in db._read("MASHI"):
        d = r.get("DATE", "")
        if d.startswith(prefix):
            try:
                day_num = int(d[8:10])
                emp_id = r.get("EMPLOYEEID")
                if emp_id:
                    day_emp_sets[day_num].add(emp_id)
            except (ValueError, IndexError):
                pass

    # Also count SPSHI type=0 (Sonderdienste, not deviations)
    for r in db._read("SPSHI"):
        d = r.get("DATE", "")
        if d.startswith(prefix) and r.get("TYPE", 0) == 0:
            try:
                day_num = int(d[8:10])
                emp_id = r.get("EMPLOYEEID")
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
        result.append(
            {
                "day": day,
                "scheduled_count": scheduled,
                "required_count": required_count,
                "status": status,
            }
        )

    return result


# ── Day schedule ─────────────────────────────────────────────
@router.get("/api/schedule/day", tags=["Schedule"], summary="Daily schedule view", description="Return the schedule for a single day, optionally filtered by group.")
def get_schedule_day(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_id: int | None = Query(None),
):
    try:
        from datetime import datetime

        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    return get_db().get_schedule_day(date, group_id=group_id)


# ── Week schedule ────────────────────────────────────────────
@router.get("/api/schedule/week", tags=["Schedule"], summary="Weekly schedule view", description="Return the schedule for an entire week, optionally filtered by group.")
def get_schedule_week(
    date: str = Query(..., description="Any date within the target week (YYYY-MM-DD)"),
    group_id: int | None = Query(None),
):
    try:
        from datetime import datetime

        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    return get_db().get_schedule_week(date, group_id=group_id)


# ── Year overview ────────────────────────────────────────────
@router.get("/api/schedule/year", tags=["Schedule"], summary="Yearly schedule overview", description="Return the yearly schedule overview for a single employee.")
def get_schedule_year(
    year: int = Query(...),
    employee_id: int = Query(...),
):
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen",
        )
    return get_db().get_schedule_year(year, employee_id)


@router.get(
    "/api/schedule/conflicts", tags=["Schedule"], summary="Schedule conflict detection"
)
def get_schedule_conflicts(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: int | None = Query(None, description="Group ID filter"),
):
    """Return all scheduling conflicts for a given month."""
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen"
        )
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen",
        )
    conflicts = get_db().get_schedule_conflicts(year, month, group_id)
    return {"conflicts": conflicts}


# ── Shift Cycles ─────────────────────────────────────────────


@router.get(
    "/api/shift-cycles", tags=["Schedule"], summary="List shift rotation cycles",
    description="Return all defined shift rotation cycles with their entries.",
)
def get_shift_cycles():
    return get_db().get_shift_cycles()


@router.get(
    "/api/shift-cycles/assign", tags=["Schedule"], summary="List cycle assignments",
    description="Return all employee-to-cycle assignments.",
)
def get_cycle_assignments():
    return get_db().get_cycle_assignments()


@router.get(
    "/api/shift-cycles/{cycle_id}", tags=["Schedule"], summary="Get shift cycle by ID",
    description="Return a single shift cycle by ID with its entries.",
)
def get_shift_cycle(cycle_id: int):
    c = get_db().get_shift_cycle(cycle_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Zyklus nicht gefunden")
    return c


class CycleAssignBody(BaseModel):
    employee_id: int = Field(..., gt=0)
    cycle_id: int = Field(..., gt=0)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post(
    "/api/shift-cycles/assign",
    tags=["Schedule"],
    summary="Assign employee to shift cycle",
    description="Assign an employee to a shift rotation cycle. Requires Planer role.",
)
def assign_cycle(body: CycleAssignBody, _cur_user: dict = Depends(require_planer)):
    # start_date format already validated by Pydantic pattern; also parse for calendar validity
    try:
        from datetime import datetime

        datetime.strptime(body.start_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    db = get_db()
    # Referential integrity: verify employee and cycle exist
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden"
        )
    if db.get_shift_cycle(body.cycle_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Schichtmodell {body.cycle_id} nicht gefunden"
        )
    try:
        result = db.assign_cycle(body.employee_id, body.cycle_id, body.start_date)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/shift-cycles/assign/{employee_id}",
    tags=["Schedule"],
    summary="Remove employee from shift cycle",
    description="Remove an employee from their assigned shift cycle. Requires Planer role.",
)
def remove_cycle_assignment(
    employee_id: int, _cur_user: dict = Depends(require_planer)
):
    try:
        count = get_db().remove_cycle_assignment(employee_id)
        return {"ok": True, "removed": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Shift Cycle CRUD ──────────────────────────────────────────


class ShiftCycleCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    size_weeks: int = Field(..., ge=1, le=52)


class CycleEntryItem(BaseModel):
    index: int = Field(..., ge=0)
    shift_id: int | None = Field(None, gt=0)


class ShiftCycleUpdateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    size_weeks: int = Field(..., ge=1, le=52)
    entries: list[CycleEntryItem] = []


@router.post("/api/shift-cycles", tags=["Schedule"], summary="Create shift cycle", description="Create a new shift rotation cycle. Requires Planer role.")
def create_shift_cycle(
    body: ShiftCycleCreateBody, _cur_user: dict = Depends(require_planer)
):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    if body.size_weeks < 1 or body.size_weeks > 52:
        raise HTTPException(
            status_code=400, detail="Anzahl Wochen muss zwischen 1 und 52 liegen"
        )
    try:
        result = get_db().create_shift_cycle(body.name.strip(), body.size_weeks)
        return {"ok": True, "cycle": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.put(
    "/api/shift-cycles/{cycle_id}", tags=["Schedule"], summary="Update shift cycle",
    description="Update an existing shift rotation cycle and its entries. Requires Planer role.",
)
def update_shift_cycle(
    cycle_id: int, body: ShiftCycleUpdateBody, _cur_user: dict = Depends(require_planer)
):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    if body.size_weeks < 1 or body.size_weeks > 52:
        raise HTTPException(
            status_code=400, detail="Anzahl Wochen muss zwischen 1 und 52 liegen"
        )
    db = get_db()
    try:
        db.update_shift_cycle(cycle_id, body.name.strip(), body.size_weeks)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)
    # Replace all entries: clear old ones, write new ones.
    # On partial failure we log the error but do not leave the cycle completely
    # empty — we re-apply what we managed to write so the caller gets a clear error.
    errors = []
    db.clear_cycle_entries(cycle_id)
    for entry in body.entries:
        if entry.shift_id:
            try:
                db.set_cycle_entry(cycle_id, entry.index, entry.shift_id)
            except Exception as exc:
                errors.append({"index": entry.index, "error": str(exc)})
    if errors:
        cycle = db.get_shift_cycle(cycle_id)
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Zyklus teilweise gespeichert — {len(errors)} Einträge fehlgeschlagen",
                "errors": errors,
                "cycle": cycle,
            },
        )
    cycle = db.get_shift_cycle(cycle_id)
    return {"ok": True, "cycle": cycle}


@router.delete(
    "/api/shift-cycles/{cycle_id}", tags=["Schedule"], summary="Delete shift cycle",
    description="Delete a shift rotation cycle. Requires Planer role.",
)
def delete_shift_cycle(cycle_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_shift_cycle(cycle_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Zyklus nicht gefunden")
        return {"ok": True, "deleted": cycle_id}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


# ── Schedule Templates (Schicht-Vorlagen & Favoriten) ────────
# NOTE: these routes must be registered BEFORE the generic
#       DELETE /api/schedule/{employee_id}/{date} route to avoid
#       "templates" being parsed as an employee_id integer.


class TemplateAssignment(BaseModel):
    employee_id: int = Field(..., gt=0)
    weekday_offset: int = Field(..., ge=0, le=6)  # 0=Mon … 6=Sun
    shift_id: int = Field(..., gt=0)
    employee_name: str | None = Field(None, max_length=200)
    shift_name: str | None = Field(None, max_length=100)


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    assignments: list[TemplateAssignment]


class TemplateApplyRequest(BaseModel):
    target_date: str = Field(
        ..., pattern=r"^\d{4}-\d{2}-\d{2}$"
    )  # ISO date string — the Monday (or any anchor) of the target week
    force: bool = False  # overwrite existing entries?


class TemplateCaptureRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    week_start_day: int = Field(
        ..., ge=1, le=31
    )  # day-of-month (1-based) of the Monday to capture
    group_id: int | None = Field(None, gt=0)


@router.get(
    "/api/schedule/templates", tags=["Schedule"], summary="List schedule templates",
    description="List all saved schedule templates.",
)
def list_templates():
    """List all saved schedule templates."""
    db = get_db()
    return db.get_schedule_templates()


@router.post(
    "/api/schedule/templates", tags=["Schedule"], summary="Create schedule template",
    description="Create a new schedule template.",
)
def create_template(body: TemplateCreate, _cur_user: dict = Depends(require_planer)):
    """Create a new schedule template."""
    db = get_db()
    assignments = [a.dict() for a in body.assignments]
    template = db.create_schedule_template(
        name=body.name,
        description=body.description,
        assignments=assignments,
    )
    return template


@router.post(
    "/api/schedule/templates/capture",
    tags=["Schedule"],
    summary="Capture week as template",
    description="Capture the current week's schedule as a reusable template. Requires Planer role.",
)
def capture_template(
    body: TemplateCaptureRequest, _cur_user: dict = Depends(require_planer)
):
    """Capture the current week's schedule entries as a new template."""
    db = get_db()
    entries = db.get_week_entries_for_template(
        year=body.year,
        month=body.month,
        week_start_day=body.week_start_day,
        group_id=body.group_id,
    )
    if not entries:
        raise HTTPException(
            status_code=400, detail="Keine Schicht-Einträge in dieser Woche gefunden"
        )
    assignments = [
        {
            "employee_id": e.get("employee_id"),
            "weekday_offset": e.get("weekday_offset", 0),
            "shift_id": e.get("shift_id"),
            "employee_name": e.get("employee_name", ""),
            "shift_name": e.get("display_name", "") or e.get("shift_name", ""),
        }
        for e in entries
    ]
    template = db.create_schedule_template(
        name=body.name,
        description=body.description,
        assignments=assignments,
    )
    return template


@router.delete(
    "/api/schedule/templates/{template_id}",
    tags=["Schedule"],
    summary="Delete schedule template",
    description="Delete a schedule template by ID.",
)
def delete_template(template_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a schedule template by ID."""
    db = get_db()
    ok = db.delete_schedule_template(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    return {"deleted": True, "id": template_id}


@router.post(
    "/api/schedule/templates/{template_id}/apply",
    tags=["Schedule"],
    summary="Apply template to week",
    description="Apply a schedule template to a target week. Use force=true to overwrite existing entries. Requires Planer role.",
)
def apply_template(
    template_id: int,
    body: TemplateApplyRequest,
    _cur_user: dict = Depends(require_planer),
):
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
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    shift_id: int = Field(..., gt=0)

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt

        try:
            _dtt.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Datum muss ein gültiges Datum im Format YYYY-MM-DD sein")
        return v


def _parse_time_range(startend: str):
    """Parse a 'HH:MM-HH:MM' string into (start_minutes, end_minutes) from midnight.

    Returns None if the string cannot be parsed.
    Handles overnight shifts where end < start by adding 24h to end.
    """
    if not startend or "-" not in startend:
        return None
    parts = startend.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        sh, sm = parts[0].strip().split(":")
        eh, em = parts[1].strip().split(":")
        start_min = int(sh) * 60 + int(sm)
        end_min = int(eh) * 60 + int(em)
        if end_min <= start_min:
            end_min += 24 * 60  # overnight shift
        return (start_min, end_min)
    except (ValueError, IndexError):
        return None


def _times_overlap(range_a, range_b) -> bool:
    """Check if two (start_min, end_min) ranges overlap."""
    if range_a is None or range_b is None:
        return False
    return range_a[0] < range_b[1] and range_b[0] < range_a[1]


def _get_shift_time_range(shift: dict, weekday_index: int):
    """Get the time range for a shift on a specific weekday (0=Mon..6=Sun).

    Falls back to STARTEND0 if the weekday-specific field is empty.
    """
    # Try weekday-specific time first
    key = f"STARTEND{weekday_index}"
    val = (shift.get(key, "") or "").strip()
    if val and "-" in val:
        result = _parse_time_range(val)
        if result:
            return result
    # Fall back to STARTEND0 (Monday / default)
    val0 = (shift.get("STARTEND0", "") or "").strip()
    return _parse_time_range(val0)


@router.post(
    "/api/schedule",
    tags=["Schedule"],
    summary="Add schedule entry",
    description="Assign a shift to an employee on a specific date. Requires Planer role.",
)
def create_schedule_entry(
    body: ScheduleEntryCreate, _cur_user: dict = Depends(require_planer)
):
    from datetime import date as _date

    from sp5lib.dbf_reader import get_table_fields
    from sp5lib.dbf_writer import find_all_records

    # Date validation handled by Pydantic model
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden"
        )
    new_shift = db.get_shift(body.shift_id)
    if new_shift is None:
        raise HTTPException(
            status_code=404, detail=f"Schicht {body.shift_id} nicht gefunden"
        )

    entry_date = _date.fromisoformat(body.date)
    iso_wd = entry_date.isoweekday()  # 1=Mon, 7=Sun
    weekday_index = iso_wd - 1  # 0=Mon, 6=Sun (for STARTEND0..STARTEND6)

    # ── Conflict Check 1: Duplicate assignment (same employee + same shift + same date) ──
    try:
        filepath = db._table("MASHI")
        fields = get_table_fields(filepath)
        existing_entries = find_all_records(
            filepath, fields, EMPLOYEEID=body.employee_id, DATE=body.date
        )
        for _, rec in existing_entries:
            if rec.get("SHIFTID") == body.shift_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "type": "duplicate_assignment",
                        "message": f"Mitarbeiter {body.employee_id} ist am {body.date} bereits "
                                   f"der Schicht '{new_shift.get('NAME', body.shift_id)}' zugewiesen.",
                        "employee_id": body.employee_id,
                        "date": body.date,
                        "shift_id": body.shift_id,
                    },
                )
    except HTTPException:
        raise
    except Exception:
        pass  # best-effort

    # ── Conflict Check 2: Overlapping shifts (time-based) ──
    try:
        new_time_range = _get_shift_time_range(new_shift, weekday_index)
        if new_time_range and existing_entries:
            for _, rec in existing_entries:
                existing_shift_id = rec.get("SHIFTID")
                if existing_shift_id:
                    existing_shift = db.get_shift(existing_shift_id)
                    if existing_shift:
                        existing_range = _get_shift_time_range(existing_shift, weekday_index)
                        if _times_overlap(new_time_range, existing_range):
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "type": "overlapping_shift",
                                    "message": (
                                        f"Mitarbeiter {body.employee_id} hat am {body.date} bereits "
                                        f"die Schicht '{existing_shift.get('NAME', existing_shift_id)}' "
                                        f"die sich zeitlich mit '{new_shift.get('NAME', body.shift_id)}' überschneidet."
                                    ),
                                    "employee_id": body.employee_id,
                                    "date": body.date,
                                    "existing_shift_id": existing_shift_id,
                                    "new_shift_id": body.shift_id,
                                },
                            )
        # Also check SPSHI (Sonderdienste) for overlaps
        spshi_path = db._table("SPSHI")
        spshi_fields = get_table_fields(spshi_path)
        spshi_entries = find_all_records(
            spshi_path, spshi_fields, EMPLOYEEID=body.employee_id, DATE=body.date
        )
        if new_time_range and spshi_entries:
            for _, rec in spshi_entries:
                spshi_startend = (rec.get("STARTEND", "") or "").strip()
                spshi_range = _parse_time_range(spshi_startend)
                if _times_overlap(new_time_range, spshi_range):
                    spshi_name = rec.get("NAME", "Sonderdienst")
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "type": "overlapping_shift",
                            "message": (
                                f"Mitarbeiter {body.employee_id} hat am {body.date} bereits "
                                f"den Sonderdienst '{spshi_name}' der sich zeitlich mit "
                                f"'{new_shift.get('NAME', body.shift_id)}' überschneidet."
                            ),
                            "employee_id": body.employee_id,
                            "date": body.date,
                            "new_shift_id": body.shift_id,
                        },
                    )
    except HTTPException:
        raise
    except Exception:
        pass  # best-effort — don't block on unexpected errors

    # ── Conflict Check 3: Absence/vacation on same day ──
    try:
        absen_path = db._table("ABSEN")
        absen_fields = get_table_fields(absen_path)
        absence_entries = find_all_records(
            absen_path, absen_fields, EMPLOYEEID=body.employee_id, DATE=body.date
        )
        if absence_entries:
            _, absence_rec = absence_entries[0]
            leave_type_id = absence_rec.get("LEAVETYPID")
            leave_type = db.get_leave_type(leave_type_id) if leave_type_id else None
            leave_name = leave_type.get("NAME", "Abwesenheit") if leave_type else "Abwesenheit"
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "absence_conflict",
                    "message": (
                        f"Mitarbeiter {body.employee_id} hat am {body.date} bereits "
                        f"eine Abwesenheit eingetragen: '{leave_name}'. "
                        f"Schichtzuweisung nicht möglich."
                    ),
                    "employee_id": body.employee_id,
                    "date": body.date,
                    "leave_type": leave_name,
                },
            )
    except HTTPException:
        raise
    except Exception:
        pass  # best-effort

    # ── Conflict Check 4: RESTR restrictions ──
    # Check RESTR restrictions: weekday 0=all days, 1=Mon...7=Sun (ISO weekday)
    try:
        restrictions = db._read("RESTR")
        for r in restrictions:
            if (
                r.get("EMPLOYEEID") == body.employee_id
                and r.get("SHIFTID") == body.shift_id
            ):
                wday = r.get("WEEKDAY", 0) or 0
                if wday == 0 or wday == iso_wd:
                    reason = (r.get("RESERVED") or "").strip()
                    detail = (
                        f"Mitarbeiter {body.employee_id} hat eine Einschränkung für "
                        f"Schicht {body.shift_id}"
                        + (f" an Wochentag {iso_wd}" if wday != 0 else "")
                        + (f": {reason}" if reason else "")
                    )
                    raise HTTPException(status_code=409, detail=detail)
    except HTTPException:
        raise
    except Exception:
        pass  # RESTR check is best-effort; don't block scheduling on unexpected errors

    try:
        result = db.add_schedule_entry(body.employee_id, body.date, body.shift_id)
        broadcast(
            "schedule_changed", {"employee_id": body.employee_id, "date": body.date}
        )
        # Audit: schedule entry created
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="CREATE",
            entity="schedule",
            entity_id=body.employee_id,
            details=f"Schicht {body.shift_id} für Mitarbeiter {body.employee_id} am {body.date}",
            new_value={"employee_id": body.employee_id, "date": body.date, "shift_id": body.shift_id},
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/schedule/{employee_id}/{date}",
    tags=["Schedule"],
    summary="Delete schedule entry",
    description="Remove a scheduled shift for an employee on a specific date (YYYY-MM-DD). Requires Planer role.",
)
def delete_schedule_entry(
    employee_id: int, date: str, _cur_user: dict = Depends(require_planer)
):
    try:
        from datetime import datetime

        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    try:
        db = get_db()
        count = db.delete_schedule_entry(employee_id, date)
        if count == 0:
            raise HTTPException(
                status_code=404, detail="Plantafel-Eintrag nicht gefunden"
            )
        broadcast("schedule_changed", {"employee_id": employee_id, "date": date})
        # Audit: schedule entry deleted
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="DELETE",
            entity="schedule",
            entity_id=employee_id,
            details=f"Schichteintrag für Mitarbeiter {employee_id} am {date} gelöscht",
            old_value={"employee_id": employee_id, "date": date},
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/schedule-shift/{employee_id}/{date}",
    tags=["Schedule"],
    summary="Delete shift override",
    description="Remove only the shift entry for an employee on a given date, leaving absences intact. Requires Planer role.",
)
def delete_shift_only(
    employee_id: int, date: str, _cur_user: dict = Depends(require_planer)
):
    """Delete only shift entries (MASHI/SPSHI) for an employee on a date, leaving absences intact."""
    try:
        from datetime import datetime

        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    try:
        count = get_db().delete_shift_only(employee_id, date)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Generate schedule from cycle ────────────────────────────
class ScheduleGenerateRequest(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    employee_ids: list[int] | None = None
    force: bool = False
    dry_run: bool = False
    respect_restrictions: bool = True


@router.post(
    "/api/schedule/generate",
    tags=["Schedule"],
    summary="Auto-generate schedule from cycles",
    description="Auto-generate schedule entries for a month from shift-cycle assignments. Use `dry_run=true` for preview without writing. Requires Planer role.",
)
@limiter.limit("10/minute")
def generate_schedule(
    request: Request, body: ScheduleGenerateRequest, _cur_user: dict = Depends(require_planer)
):
    """Generate (or preview) schedule entries for a month based on cycle assignments.
    dry_run=True: returns preview without writing.
    respect_restrictions=True: skips shifts that employee has a restriction for."""
    if not (1 <= body.month <= 12):
        raise HTTPException(
            status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen"
        )
    try:
        result = get_db().generate_schedule_from_cycle(
            year=body.year,
            month=body.month,
            employee_ids=body.employee_ids,
            force=body.force,
            dry_run=body.dry_run,
            respect_restrictions=body.respect_restrictions,
        )
        created = result["created"]
        skipped = result["skipped"]
        skipped_restriction = result.get("skipped_restriction", 0)
        errors = result.get("errors", [])
        preview = result.get("preview", [])
        report = result.get("report", {})
        if body.dry_run:
            message = (
                f"Vorschau: {created} Einträge würden erstellt, {skipped} übersprungen"
            )
        else:
            message = f"{created} Einträge erstellt, {skipped} übersprungen"
        if skipped_restriction:
            message += f", {skipped_restriction} wegen Sperren übersprungen"
        if errors:
            message += f", {len(errors)} Fehler"
        return {
            "created": created,
            "skipped": skipped,
            "skipped_restriction": skipped_restriction,
            "errors": errors,
            "preview": preview,
            "report": report,
            "message": message,
        }
    except Exception as e:
        raise _sanitize_500(e)


# ── Restrictions ──────────────────────────────────────────────


@router.get(
    "/api/restrictions", tags=["Schedule"], summary="List employee shift restrictions",
    description="Return all shift restrictions, optionally filtered by employee_id.",
)
def get_restrictions(employee_id: int | None = Query(None)):
    """Return all shift restrictions, optionally filtered by employee_id."""
    return get_db().get_restrictions(employee_id=employee_id)


class RestrictionCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    shift_id: int = Field(..., gt=0)
    reason: str | None = Field("", max_length=500)
    weekday: int | None = Field(0, ge=0, le=7)


@router.post("/api/restrictions", tags=["Schedule"], summary="Add shift restriction", description="Add a shift restriction preventing an employee from a specific shift. Requires Admin role.")
def set_restriction(body: RestrictionCreate, _cur_user: dict = Depends(require_admin)):
    """Add a shift restriction for an employee."""
    weekday = body.weekday or 0
    if not (0 <= weekday <= 7):
        raise HTTPException(
            status_code=400,
            detail="weekday muss zwischen 0 (alle Wochentage) und 7 (So) liegen — 1=Mo, 2=Di, ..., 7=So",
        )
    try:
        result = get_db().set_restriction(
            employee_id=body.employee_id,
            shift_id=body.shift_id,
            reason=body.reason or "",
            weekday=weekday,
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/restrictions/{employee_id}/{shift_id}",
    tags=["Schedule"],
    summary="Remove shift restriction",
    description="Remove a shift restriction for an employee.",
)
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
        raise _sanitize_500(e)


# ── Bulk Schedule Operations ─────────────────────────────────


class BulkEntry(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    shift_id: int | None = Field(None, gt=0)


class BulkScheduleBody(BaseModel):
    entries: list[BulkEntry]
    overwrite: bool = True


@router.post(
    "/api/schedule/bulk",
    tags=["Schedule"],
    summary="Bulk schedule operations",
    description="Create, update, or delete multiple schedule entries in a single request. If `shift_id` is null the entry is deleted. Requires Planer role.",
)
def bulk_schedule(body: BulkScheduleBody, _cur_user: dict = Depends(require_planer)):
    """Bulk create/update/delete schedule entries in a single request.
    If shift_id is null the entry is deleted; otherwise created or overwritten."""
    from datetime import datetime as _dt2

    created = 0
    updated = 0
    deleted = 0
    db = get_db()
    # Pre-validate all shift_ids to avoid partial writes with bad data
    shift_id_cache: dict = {}
    for entry in body.entries:
        if entry.shift_id is not None and entry.shift_id not in shift_id_cache:
            shift_id_cache[entry.shift_id] = db.get_shift(entry.shift_id)
        if entry.shift_id is not None and shift_id_cache.get(entry.shift_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Schicht {entry.shift_id} nicht gefunden"
            )
    for entry in body.entries:
        try:
            _dt2.strptime(entry.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Ungültiges Datumsformat: {entry.date}"
            )
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
            raise _sanitize_500(e)
    return {"created": created, "updated": updated, "deleted": deleted}


# ── Bulk Group Assignment ────────────────────────────────────


class BulkGroupAssignBody(BaseModel):
    """Assign a single shift to all members of a group (or explicit employee list) across a date range."""
    group_id: int | None = Field(None, gt=0, description="Group whose members receive the shift")
    employee_ids: list[int] | None = Field(None, description="Explicit employee IDs (alternative to group_id)")
    shift_id: int = Field(..., gt=0, description="Shift to assign")
    date_from: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Start date (inclusive)")
    date_to: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="End date (inclusive)")
    overwrite: bool = Field(True, description="Overwrite existing entries")


@router.post(
    "/api/schedule/bulk-group",
    tags=["Schedule"],
    summary="Bulk assign shift to group",
    description="Assign a shift to all members of a group (or explicit employee list) for a date range. Requires Planer role.",
)
def bulk_group_assign(body: BulkGroupAssignBody, _cur_user: dict = Depends(require_planer)):
    """Assign one shift to a group of employees across a date range."""
    from datetime import datetime as _dt3
    from datetime import timedelta

    if not body.group_id and not body.employee_ids:
        raise HTTPException(status_code=400, detail="group_id oder employee_ids muss angegeben werden")

    db = get_db()

    # Validate shift exists
    shift = db.get_shift(body.shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail=f"Schicht {body.shift_id} nicht gefunden")

    # Resolve employee IDs
    if body.employee_ids:
        emp_ids = body.employee_ids
    else:
        emp_ids = db.get_group_members(body.group_id)
        if not emp_ids:
            raise HTTPException(status_code=404, detail=f"Gruppe {body.group_id} hat keine Mitglieder")

    # Parse and validate dates
    try:
        d_from = _dt3.strptime(body.date_from, "%Y-%m-%d").date()
        d_to = _dt3.strptime(body.date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat")

    if d_from > d_to:
        raise HTTPException(status_code=400, detail="date_from muss vor date_to liegen")

    if (d_to - d_from).days > 366:
        raise HTTPException(status_code=400, detail="Maximaler Zeitraum: 366 Tage")

    # Build date list
    dates: list[str] = []
    current = d_from
    while current <= d_to:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    created = 0
    updated = 0
    skipped = 0
    for emp_id in emp_ids:
        for date_str in dates:
            try:
                if body.overwrite:
                    old_count = db.delete_schedule_entry(emp_id, date_str)
                    db.add_schedule_entry(emp_id, date_str, body.shift_id)
                    if old_count > 0:
                        updated += 1
                    else:
                        created += 1
                else:
                    # Try to add; if it already exists, skip
                    try:
                        db.add_schedule_entry(emp_id, date_str, body.shift_id)
                        created += 1
                    except ValueError:
                        skipped += 1
            except Exception as e:
                raise _sanitize_500(e)

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "employees": len(emp_ids),
        "days": len(dates),
        "total_assignments": created + updated,
    }


# ── Einsatzplan Write (SPSHI) ────────────────────────────────


class EinsatzplanCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str | None = Field("", max_length=100)
    shortname: str | None = Field("", max_length=20)
    shift_id: int | None = Field(0, ge=0)
    workplace_id: int | None = Field(0, ge=0)
    startend: str | None = Field("", max_length=20)
    duration: float | None = Field(0.0, ge=0.0)
    colortext: int | None = Field(0, ge=0, le=16777215)
    colorbar: int | None = Field(0, ge=0, le=16777215)
    colorbk: int | None = Field(16777215, ge=0, le=16777215)


class EinsatzplanUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    shortname: str | None = Field(None, max_length=20)
    shift_id: int | None = Field(None, ge=0)
    workplace_id: int | None = Field(None, ge=0)
    startend: str | None = Field(None, max_length=20)
    duration: float | None = Field(None, ge=0.0)
    colortext: int | None = Field(None, ge=0, le=16777215)
    colorbar: int | None = Field(None, ge=0, le=16777215)
    colorbk: int | None = Field(None, ge=0, le=16777215)


class DeviationCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str | None = Field("Arbeitszeitabweichung", max_length=100)
    shortname: str | None = Field("AZA", max_length=20)
    startend: str | None = Field("", max_length=20)  # e.g. "07:00-15:30"
    duration: float | None = Field(0.0, ge=0.0)  # minutes or hours (stores raw)
    colortext: int | None = Field(0, ge=0, le=16777215)
    colorbar: int | None = Field(0, ge=0, le=16777215)
    colorbk: int | None = Field(16744448, ge=0, le=16777215)  # orange-ish default


@router.post(
    "/api/einsatzplan", tags=["Schedule"], summary="Create deployment plan entry",
    description="Create a Sonderdienst (special duty) entry. Requires Planer role.",
)
def create_einsatzplan_entry(
    body: EinsatzplanCreate, _cur_user: dict = Depends(require_planer)
):
    """Create a Sonderdienst entry in SPSHI (TYPE=0)."""
    try:
        from datetime import datetime

        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden"
        )
    try:
        result = db.add_spshi_entry(
            employee_id=body.employee_id,
            date_str=body.date,
            name=body.name or "",
            shortname=body.shortname or "",
            shift_id=body.shift_id or 0,
            workplace_id=body.workplace_id or 0,
            entry_type=0,
            startend=body.startend or "",
            duration=body.duration or 0.0,
            colortext=body.colortext or 0,
            colorbar=body.colorbar or 0,
            colorbk=body.colorbk if body.colorbk is not None else 16777215,
        )
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.put(
    "/api/einsatzplan/{entry_id}",
    tags=["Schedule"],
    summary="Update deployment plan entry",
    description="Update an existing deployment plan entry. Requires Planer role.",
)
def update_einsatzplan_entry(
    entry_id: int, body: EinsatzplanUpdate, _cur_user: dict = Depends(require_planer)
):
    """Update an existing SPSHI entry."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    # Map frontend keys to DBF field names
    key_map = {
        "name": "NAME",
        "shortname": "SHORTNAME",
        "shift_id": "SHIFTID",
        "workplace_id": "WORKPLACID",
        "startend": "STARTEND",
        "duration": "DURATION",
        "colortext": "COLORTEXT",
        "colorbar": "COLORBAR",
        "colorbk": "COLORBK",
    }
    mapped = {key_map.get(k, k.upper()): v for k, v in data.items()}
    try:
        result = get_db().update_spshi_entry(entry_id, mapped)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/einsatzplan/{entry_id}",
    tags=["Schedule"],
    summary="Delete deployment plan entry",
    description="Delete a SPSHI entry by ID.",
)
def delete_einsatzplan_entry(entry_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a SPSHI entry by ID."""
    try:
        count = get_db().delete_spshi_entry_by_id(entry_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="SPSHI entry not found")
        return {"ok": True, "deleted": entry_id}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.post(
    "/api/einsatzplan/deviation",
    tags=["Schedule"],
    summary="Record deployment deviation",
    description="Create an Arbeitszeitabweichung entry in SPSHI (TYPE=1).",
)
def create_deviation(body: DeviationCreate, _cur_user: dict = Depends(require_planer)):
    """Create an Arbeitszeitabweichung entry in SPSHI (TYPE=1)."""
    try:
        from datetime import datetime

        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden"
        )
    try:
        result = db.add_spshi_entry(
            employee_id=body.employee_id,
            date_str=body.date,
            name=body.name or "Arbeitszeitabweichung",
            shortname=body.shortname or "AZA",
            shift_id=0,
            workplace_id=0,
            entry_type=1,
            startend=body.startend or "",
            duration=body.duration or 0.0,
            colortext=body.colortext or 0,
            colorbar=body.colorbar or 0,
            colorbk=body.colorbk if body.colorbk is not None else 16744448,
        )
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.get(
    "/api/einsatzplan", tags=["Schedule"], summary="List deployment plan entries",
    description="Return deployment plan entries (Sonderdienste + deviations) for a specific date.",
)
def get_einsatzplan(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_id: int | None = Query(None),
):
    """Return SPSHI entries for a specific date (Sonderdienste + Abweichungen)."""
    try:
        from datetime import datetime

        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    return get_db().get_spshi_entries_for_day(date, group_id=group_id)


# ── Cycle Exceptions ─────────────────────────────────────────


class CycleExceptionSet(BaseModel):
    employee_id: int = Field(..., gt=0)
    cycle_assignment_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    type: int = Field(1, ge=0, le=1)  # 1=skip, 0=normal


@router.get("/api/cycle-exceptions", tags=["Schedule"], summary="List cycle exceptions", description="Return cycle exceptions (date overrides in assigned cycles).")
def get_cycle_exceptions(
    employee_id: int | None = Query(None),
    cycle_assignment_id: int | None = Query(None),
):
    """Get cycle exceptions (date overrides in assigned cycles)."""
    return get_db().get_cycle_exceptions(
        employee_id=employee_id, cycle_assignment_id=cycle_assignment_id
    )


@router.post(
    "/api/cycle-exceptions", tags=["Schedule"], summary="Create cycle exception",
    description="Set a cycle exception for a specific date. Requires Planer role.",
)
def set_cycle_exception(
    body: CycleExceptionSet, _cur_user: dict = Depends(require_planer)
):
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
        raise _sanitize_500(e)


@router.delete(
    "/api/cycle-exceptions/{exception_id}",
    tags=["Schedule"],
    summary="Delete cycle exception",
    description="Delete a cycle exception by ID. Requires Planer role.",
)
def delete_cycle_exception(
    exception_id: int, _cur_user: dict = Depends(require_planer)
):
    """Delete a cycle exception by ID."""
    count = get_db().delete_cycle_exception(exception_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Ausnahme nicht gefunden")
    return {"ok": True, "deleted": exception_id}


# ── Woche kopieren ─────────────────────────────────────────────
class SwapShiftsRequest(BaseModel):
    employee_id_1: int = Field(..., gt=0)
    employee_id_2: int = Field(..., gt=0)
    dates: list[str] = Field(..., min_length=1, max_length=366)  # YYYY-MM-DD strings


@router.post(
    "/api/schedule/swap",
    tags=["Schedule"],
    summary="Swap shifts between employees",
    description="Exchange schedule entries (shifts and absences) between two employees for the specified dates. Requires Planer role.",
)
def swap_shifts(body: SwapShiftsRequest, _cur_user: dict = Depends(require_planer)):
    """Swap schedule entries (shifts + absences) between two employees for the given dates."""
    from datetime import datetime as _dt3

    from sp5lib.dbf_reader import get_table_fields
    from sp5lib.dbf_writer import find_all_records

    if body.employee_id_1 == body.employee_id_2:
        raise HTTPException(
            status_code=400, detail="Beide Mitarbeiter müssen verschieden sein"
        )
    if not body.dates:
        raise HTTPException(status_code=400, detail="Mindestens ein Datum erforderlich")
    for d in body.dates:
        try:
            _dt3.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")

    db = get_db()
    swapped = 0
    errors = []

    def collect_entries(emp_id: int, date_str: str):
        result = []
        for table, kind in [
            ("MASHI", "shift"),
            ("SPSHI", "special_shift"),
            ("ABSEN", "absence"),
        ]:
            filepath = db._table(table)
            fields = get_table_fields(filepath)
            matches = find_all_records(
                filepath, fields, EMPLOYEEID=emp_id, DATE=date_str
            )
            for _, rec in matches:
                if kind == "shift":
                    result.append(
                        {
                            "kind": "shift",
                            "shift_id": rec.get("SHIFTID"),
                            "workplace_id": rec.get("WORKPLACID", 0),
                        }
                    )
                elif kind == "special_shift":
                    result.append(
                        {
                            "kind": "special_shift",
                            "shift_id": rec.get("SHIFTID"),
                            "workplace_id": rec.get("WORKPLACID", 0),
                        }
                    )
                elif kind == "absence":
                    result.append(
                        {"kind": "absence", "leave_type_id": rec.get("LEAVETYPID")}
                    )
        return result

    def write_entries(emp_id: int, date_str: str, entries):
        for entry in entries:
            try:
                if entry["kind"] == "shift":
                    db.add_schedule_entry(emp_id, date_str, entry["shift_id"])
                elif entry["kind"] == "absence" and entry.get("leave_type_id"):
                    db.add_absence(emp_id, date_str, entry["leave_type_id"])
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
            # Write crossed — on any error, attempt rollback to avoid data loss
            pre_errors = len(errors)
            write_entries(body.employee_id_1, date_str, entries2)
            write_entries(body.employee_id_2, date_str, entries1)
            if len(errors) > pre_errors:
                # Write failed partially: try to restore originals
                db.delete_schedule_entry(body.employee_id_1, date_str)
                db.delete_schedule_entry(body.employee_id_2, date_str)
                write_entries(body.employee_id_1, date_str, entries1)
                write_entries(body.employee_id_2, date_str, entries2)
                errors.append(
                    f"{date_str}: Swap fehlgeschlagen, Original wiederhergestellt"
                )
            else:
                swapped += 1
        except Exception as exc:
            errors.append(f"{date_str}: {exc}")

    return {
        "ok": True,
        "swapped_days": swapped,
        "errors": errors,
        "message": f"{swapped} Tag(e) getauscht"
        + (f", {len(errors)} Fehler" if errors else ""),
    }


class CopyWeekRequest(BaseModel):
    source_employee_id: int = Field(..., gt=0)
    dates: list[str] = Field(
        ..., min_length=1, max_length=31
    )  # YYYY-MM-DD strings (up to 7)
    target_employee_ids: list[int] = Field(..., min_length=1)
    skip_existing: bool = True  # True = don't overwrite existing entries


@router.post(
    "/api/schedule/copy-week",
    tags=["Schedule"],
    summary="Copy week schedule",
    description="Copy a source employee's schedule entries for given dates to one or more target employees. Use `skip_existing=false` to overwrite. Requires Planer role.",
)
def copy_week(body: CopyWeekRequest, _cur_user: dict = Depends(require_planer)):
    """Copy one employee's schedule entries (shifts + absences) for given dates to one or more target employees."""
    db = get_db()
    if not body.dates or not body.target_employee_ids:
        raise HTTPException(
            status_code=400, detail="dates and target_employee_ids must not be empty"
        )

    # Validate dates
    from datetime import datetime as _dt2

    for d in body.dates:
        try:
            _dt2.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")

    # Collect source entries grouped by date
    # We query each date individually via the schedule tables
    from sp5lib.dbf_reader import get_table_fields
    from sp5lib.dbf_writer import find_all_records

    source_entries: dict[str, list[dict]] = {}  # date → list of entry dicts
    for date_str in body.dates:
        entries_for_date = []
        for table, kind in [
            ("MASHI", "shift"),
            ("SPSHI", "special_shift"),
            ("ABSEN", "absence"),
        ]:
            filepath = db._table(table)
            fields = get_table_fields(filepath)
            matches = find_all_records(
                filepath, fields, EMPLOYEEID=body.source_employee_id, DATE=date_str
            )
            for _, rec in matches:
                if kind == "shift":
                    entries_for_date.append(
                        {
                            "kind": "shift",
                            "shift_id": rec.get("SHIFTID"),
                            "workplace_id": rec.get("WORKPLACID", 0),
                        }
                    )
                elif kind == "special_shift":
                    entries_for_date.append(
                        {
                            "kind": "special_shift",
                            "shift_id": rec.get("SHIFTID"),
                            "workplace_id": rec.get("WORKPLACID", 0),
                        }
                    )
                elif kind == "absence":
                    entries_for_date.append(
                        {"kind": "absence", "leave_type_id": rec.get("LEAVETYPID")}
                    )
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
                for table in ["MASHI", "SPSHI", "ABSEN"]:
                    filepath = db._table(table)
                    fields = get_table_fields(filepath)
                    if find_all_records(
                        filepath, fields, EMPLOYEEID=target_id, DATE=date_str
                    ):
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
                    if entry["kind"] == "shift":
                        db.add_schedule_entry(target_id, date_str, entry["shift_id"])
                        created += 1
                    elif entry["kind"] == "absence" and entry.get("leave_type_id"):
                        db.add_absence(target_id, date_str, entry["leave_type_id"])
                        created += 1
                    # special_shift: skip for now (complex custom fields)
                except Exception as exc:
                    errors.append(f"MA {target_id} / {date_str}: {exc}")

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "message": f"{created} Einträge kopiert, {skipped} übersprungen"
        + (f", {len(errors)} Fehler" if errors else ""),
    }
