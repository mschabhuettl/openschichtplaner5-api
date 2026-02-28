"""Schedule, shift-cycles, staffing, einsatzplan, restrictions router."""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from ..dependencies import (
    get_db, require_admin, require_planer, _sanitize_500,
)
from .events import broadcast

router = APIRouter()



@router.get("/api/schedule", tags=["Schedule"], summary="Get monthly schedule", description="Return the full schedule grid for a given year/month, optionally filtered by group.")
def get_schedule(
    year: int = Query(..., description="Year"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: Optional[int] = Query(None, description="Filter by group ID")
):
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")
    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen")
    return get_db().get_schedule(year=year, month=month, group_id=group_id)


@router.get("/api/cycles", tags=["Schedule"], summary="List schedule cycles")
def get_cycles():
    return get_db().get_cycles()


# ── Staffing requirements ────────────────────────────────────
@router.get("/api/staffing", tags=["Schedule"], summary="Staffing overview")
def get_staffing(
    year: int = Query(...),
    month: int = Query(...),
):
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")
    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen")
    return get_db().get_staffing(year, month)


# ── Schedule Coverage (Personalbedarf-Ampel) ─────────────────
@router.get("/api/schedule/coverage", tags=["Schedule"], summary="Schedule coverage analysis")
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
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")
    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen")

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
@router.get("/api/schedule/day", tags=["Schedule"], summary="Daily schedule view")
def get_schedule_day(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_id: Optional[int] = Query(None),
):
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    return get_db().get_schedule_day(date, group_id=group_id)


# ── Week schedule ────────────────────────────────────────────
@router.get("/api/schedule/week", tags=["Schedule"], summary="Weekly schedule view")
def get_schedule_week(
    date: str = Query(..., description="Any date within the target week (YYYY-MM-DD)"),
    group_id: Optional[int] = Query(None),
):
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    return get_db().get_schedule_week(date, group_id=group_id)


# ── Year overview ────────────────────────────────────────────
@router.get("/api/schedule/year", tags=["Schedule"], summary="Yearly schedule overview")
def get_schedule_year(
    year: int = Query(...),
    employee_id: int = Query(...),
):
    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen")
    return get_db().get_schedule_year(year, employee_id)


@router.get("/api/schedule/conflicts", tags=["Schedule"], summary="Schedule conflict detection")
def get_schedule_conflicts(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    group_id: Optional[int] = Query(None, description="Group ID filter"),
):
    """Return all scheduling conflicts for a given month."""
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")
    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen")
    conflicts = get_db().get_schedule_conflicts(year, month, group_id)
    return {"conflicts": conflicts}


# ── Shift Cycles ─────────────────────────────────────────────

@router.get("/api/shift-cycles", tags=["Schedule"], summary="List shift rotation cycles")
def get_shift_cycles():
    return get_db().get_shift_cycles()


@router.get("/api/shift-cycles/assign", tags=["Schedule"], summary="List cycle assignments")
def get_cycle_assignments():
    return get_db().get_cycle_assignments()


@router.get("/api/shift-cycles/{cycle_id}", tags=["Schedule"], summary="Get shift cycle by ID")
def get_shift_cycle(cycle_id: int):
    c = get_db().get_shift_cycle(cycle_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Zyklus nicht gefunden")
    return c


class CycleAssignBody(BaseModel):
    employee_id: int = Field(..., gt=0)
    cycle_id: int = Field(..., gt=0)
    start_date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')


@router.post("/api/shift-cycles/assign", tags=["Schedule"], summary="Assign employee to shift cycle")
def assign_cycle(body: CycleAssignBody, _cur_user: dict = Depends(require_planer)):
    try:
        from datetime import datetime
        datetime.strptime(body.start_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    try:
        result = get_db().assign_cycle(body.employee_id, body.cycle_id, body.start_date)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/shift-cycles/assign/{employee_id}", tags=["Schedule"], summary="Remove employee from shift cycle")
def remove_cycle_assignment(employee_id: int, _cur_user: dict = Depends(require_planer)):
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
    shift_id: Optional[int] = Field(None, gt=0)


class ShiftCycleUpdateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    size_weeks: int = Field(..., ge=1, le=52)
    entries: List[CycleEntryItem] = []


@router.post("/api/shift-cycles", tags=["Schedule"], summary="Create shift cycle")
def create_shift_cycle(body: ShiftCycleCreateBody, _cur_user: dict = Depends(require_planer)):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein")
    if body.size_weeks < 1 or body.size_weeks > 52:
        raise HTTPException(status_code=400, detail="Anzahl Wochen muss zwischen 1 und 52 liegen")
    try:
        result = get_db().create_shift_cycle(body.name.strip(), body.size_weeks)
        return {"ok": True, "cycle": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.put("/api/shift-cycles/{cycle_id}", tags=["Schedule"], summary="Update shift cycle")
def update_shift_cycle(cycle_id: int, body: ShiftCycleUpdateBody, _cur_user: dict = Depends(require_planer)):
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
        raise _sanitize_500(e)


@router.delete("/api/shift-cycles/{cycle_id}", tags=["Schedule"], summary="Delete shift cycle")
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
    weekday_offset: int = Field(..., ge=0, le=6)   # 0=Mon … 6=Sun
    shift_id: int = Field(..., gt=0)
    employee_name: Optional[str] = Field(None, max_length=200)
    shift_name: Optional[str] = Field(None, max_length=100)


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field('', max_length=500)
    assignments: List[TemplateAssignment]


class TemplateApplyRequest(BaseModel):
    target_date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')  # ISO date string — the Monday (or any anchor) of the target week
    force: bool = False  # overwrite existing entries?


class TemplateCaptureRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field('', max_length=500)
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    week_start_day: int = Field(..., ge=1, le=31)  # day-of-month (1-based) of the Monday to capture
    group_id: Optional[int] = Field(None, gt=0)


@router.get("/api/schedule/templates", tags=["Schedule"], summary="List schedule templates")
def list_templates():
    """List all saved schedule templates."""
    db = get_db()
    return db.get_schedule_templates()


@router.post("/api/schedule/templates", tags=["Schedule"], summary="Create schedule template")
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


@router.post("/api/schedule/templates/capture", tags=["Schedule"], summary="Capture week as template")
def capture_template(body: TemplateCaptureRequest, _cur_user: dict = Depends(require_planer)):
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


@router.delete("/api/schedule/templates/{template_id}", tags=["Schedule"], summary="Delete schedule template")
def delete_template(template_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a schedule template by ID."""
    db = get_db()
    ok = db.delete_schedule_template(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    return {"deleted": True, "id": template_id}


@router.post("/api/schedule/templates/{template_id}/apply", tags=["Schedule"], summary="Apply template to week")
def apply_template(template_id: int, body: TemplateApplyRequest, _cur_user: dict = Depends(require_planer)):
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
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    shift_id: int = Field(..., gt=0)

    @field_validator('date')
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt
        try:
            _dtt.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Datum muss ein gültiges Datum im Format YYYY-MM-DD sein")
        return v


@router.post("/api/schedule", tags=["Schedule"], summary="Add schedule entry", description="Assign a shift to an employee on a specific date. Requires Planer role.")
def create_schedule_entry(body: ScheduleEntryCreate, _cur_user: dict = Depends(require_planer)):
    # Date validation handled by Pydantic model
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")
    if db.get_shift(body.shift_id) is None:
        raise HTTPException(status_code=404, detail=f"Schicht {body.shift_id} nicht gefunden")
    try:
        result = db.add_schedule_entry(body.employee_id, body.date, body.shift_id)
        broadcast("schedule_changed", {"employee_id": body.employee_id, "date": body.date})
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/schedule/{employee_id}/{date}", tags=["Schedule"], summary="Delete schedule entry")
def delete_schedule_entry(employee_id: int, date: str, _cur_user: dict = Depends(require_planer)):
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    try:
        count = get_db().delete_schedule_entry(employee_id, date)
        if count == 0:
            raise HTTPException(status_code=404, detail="Plantafel-Eintrag nicht gefunden")
        broadcast("schedule_changed", {"employee_id": employee_id, "date": date})
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/schedule-shift/{employee_id}/{date}", tags=["Schedule"], summary="Delete shift override")
def delete_shift_only(employee_id: int, date: str, _cur_user: dict = Depends(require_planer)):
    """Delete only shift entries (MASHI/SPSHI) for an employee on a date, leaving absences intact."""
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    try:
        count = get_db().delete_shift_only(employee_id, date)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Generate schedule from cycle ────────────────────────────
class ScheduleGenerateRequest(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    employee_ids: Optional[List[int]] = None
    force: bool = False
    dry_run: bool = False
    respect_restrictions: bool = True


@router.post("/api/schedule/generate", tags=["Schedule"], summary="Auto-generate schedule from cycles")
def generate_schedule(body: ScheduleGenerateRequest, _cur_user: dict = Depends(require_planer)):
    """Generate (or preview) schedule entries for a month based on cycle assignments.
    dry_run=True: returns preview without writing.
    respect_restrictions=True: skips shifts that employee has a restriction for."""
    if not (1 <= body.month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")
    try:
        result = get_db().generate_schedule_from_cycle(
            year=body.year,
            month=body.month,
            employee_ids=body.employee_ids,
            force=body.force,
            dry_run=body.dry_run,
            respect_restrictions=body.respect_restrictions,
        )
        created = result['created']
        skipped = result['skipped']
        skipped_restriction = result.get('skipped_restriction', 0)
        errors = result.get('errors', [])
        preview = result.get('preview', [])
        report = result.get('report', {})
        if body.dry_run:
            message = f"Vorschau: {created} Einträge würden erstellt, {skipped} übersprungen"
        else:
            message = f"{created} Einträge erstellt, {skipped} übersprungen"
        if skipped_restriction:
            message += f", {skipped_restriction} wegen Sperren übersprungen"
        if errors:
            message += f", {len(errors)} Fehler"
        return {
            'created': created,
            'skipped': skipped,
            'skipped_restriction': skipped_restriction,
            'errors': errors,
            'preview': preview,
            'report': report,
            'message': message,
        }
    except Exception as e:
        raise _sanitize_500(e)


# ── Restrictions ──────────────────────────────────────────────

@router.get("/api/restrictions", tags=["Schedule"], summary="List employee shift restrictions")
def get_restrictions(employee_id: Optional[int] = Query(None)):
    """Return all shift restrictions, optionally filtered by employee_id."""
    return get_db().get_restrictions(employee_id=employee_id)


class RestrictionCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    shift_id: int = Field(..., gt=0)
    reason: Optional[str] = Field('', max_length=500)
    weekday: Optional[int] = Field(0, ge=0, le=6)


@router.post("/api/restrictions", tags=["Schedule"], summary="Add shift restriction")
def set_restriction(body: RestrictionCreate, _cur_user: dict = Depends(require_admin)):
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
        raise _sanitize_500(e)


@router.delete("/api/restrictions/{employee_id}/{shift_id}", tags=["Schedule"], summary="Remove shift restriction")
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
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    shift_id: Optional[int] = Field(None, gt=0)


class BulkScheduleBody(BaseModel):
    entries: List[BulkEntry]
    overwrite: bool = True


@router.post("/api/schedule/bulk", tags=["Schedule"], summary="Bulk schedule operations")
def bulk_schedule(body: BulkScheduleBody, _cur_user: dict = Depends(require_planer)):
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
            raise HTTPException(status_code=400, detail=f"Ungültiges Datumsformat: {entry.date}")
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


# ── Einsatzplan Write (SPSHI) ────────────────────────────────

class EinsatzplanCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    name: Optional[str] = Field('', max_length=100)
    shortname: Optional[str] = Field('', max_length=20)
    shift_id: Optional[int] = Field(0, ge=0)
    workplace_id: Optional[int] = Field(0, ge=0)
    startend: Optional[str] = Field('', max_length=20)
    duration: Optional[float] = Field(0.0, ge=0.0)
    colortext: Optional[int] = Field(0, ge=0, le=16777215)
    colorbar: Optional[int] = Field(0, ge=0, le=16777215)
    colorbk: Optional[int] = Field(16777215, ge=0, le=16777215)


class EinsatzplanUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    shortname: Optional[str] = Field(None, max_length=20)
    shift_id: Optional[int] = Field(None, ge=0)
    workplace_id: Optional[int] = Field(None, ge=0)
    startend: Optional[str] = Field(None, max_length=20)
    duration: Optional[float] = Field(None, ge=0.0)
    colortext: Optional[int] = Field(None, ge=0, le=16777215)
    colorbar: Optional[int] = Field(None, ge=0, le=16777215)
    colorbk: Optional[int] = Field(None, ge=0, le=16777215)


class DeviationCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    name: Optional[str] = Field('Arbeitszeitabweichung', max_length=100)
    shortname: Optional[str] = Field('AZA', max_length=20)
    startend: Optional[str] = Field('', max_length=20)   # e.g. "07:00-15:30"
    duration: Optional[float] = Field(0.0, ge=0.0)  # minutes or hours (stores raw)
    colortext: Optional[int] = Field(0, ge=0, le=16777215)
    colorbar: Optional[int] = Field(0, ge=0, le=16777215)
    colorbk: Optional[int] = Field(16744448, ge=0, le=16777215)  # orange-ish default


@router.post("/api/einsatzplan", tags=["Schedule"], summary="Create deployment plan entry")
def create_einsatzplan_entry(body: EinsatzplanCreate, _cur_user: dict = Depends(require_planer)):
    """Create a Sonderdienst entry in SPSHI (TYPE=0)."""
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
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
        raise _sanitize_500(e)


@router.put("/api/einsatzplan/{entry_id}", tags=["Schedule"], summary="Update deployment plan entry")
def update_einsatzplan_entry(entry_id: int, body: EinsatzplanUpdate, _cur_user: dict = Depends(require_planer)):
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
        raise _sanitize_500(e)


@router.delete("/api/einsatzplan/{entry_id}", tags=["Schedule"], summary="Delete deployment plan entry")
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


@router.post("/api/einsatzplan/deviation", tags=["Schedule"], summary="Record deployment deviation")
def create_deviation(body: DeviationCreate, _cur_user: dict = Depends(require_planer)):
    """Create an Arbeitszeitabweichung entry in SPSHI (TYPE=1)."""
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
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
        raise _sanitize_500(e)


@router.get("/api/einsatzplan", tags=["Schedule"], summary="List deployment plan entries")
def get_einsatzplan(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_id: Optional[int] = Query(None),
):
    """Return SPSHI entries for a specific date (Sonderdienste + Abweichungen)."""
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    return get_db().get_spshi_entries_for_day(date, group_id=group_id)


# ── Cycle Exceptions ─────────────────────────────────────────

class CycleExceptionSet(BaseModel):
    employee_id: int = Field(..., gt=0)
    cycle_assignment_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    type: int = Field(1, ge=0, le=1)  # 1=skip, 0=normal


@router.get("/api/cycle-exceptions", tags=["Schedule"], summary="List cycle exceptions")
def get_cycle_exceptions(
    employee_id: Optional[int] = Query(None),
    cycle_assignment_id: Optional[int] = Query(None),
):
    """Get cycle exceptions (date overrides in assigned cycles)."""
    return get_db().get_cycle_exceptions(employee_id=employee_id,
                                          cycle_assignment_id=cycle_assignment_id)


@router.post("/api/cycle-exceptions", tags=["Schedule"], summary="Create cycle exception")
def set_cycle_exception(body: CycleExceptionSet, _cur_user: dict = Depends(require_planer)):
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


@router.delete("/api/cycle-exceptions/{exception_id}", tags=["Schedule"], summary="Delete cycle exception")
def delete_cycle_exception(exception_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a cycle exception by ID."""
    count = get_db().delete_cycle_exception(exception_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Cycle exception not found")
    return {"ok": True, "deleted": exception_id}


# ── Woche kopieren ─────────────────────────────────────────────
class SwapShiftsRequest(BaseModel):
    employee_id_1: int = Field(..., gt=0)
    employee_id_2: int = Field(..., gt=0)
    dates: List[str] = Field(..., min_length=1, max_length=366)  # YYYY-MM-DD strings


@router.post("/api/schedule/swap", tags=["Schedule"], summary="Swap shifts between employees")
def swap_shifts(body: SwapShiftsRequest, _cur_user: dict = Depends(require_planer)):
    """Swap schedule entries (shifts + absences) between two employees for the given dates."""
    from sp5lib.dbf_reader import get_table_fields
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
    source_employee_id: int = Field(..., gt=0)
    dates: List[str] = Field(..., min_length=1, max_length=31)  # YYYY-MM-DD strings (up to 7)
    target_employee_ids: List[int] = Field(..., min_length=1)
    skip_existing: bool = True     # True = don't overwrite existing entries


@router.post("/api/schedule/copy-week", tags=["Schedule"], summary="Copy week schedule")
def copy_week(body: CopyWeekRequest, _cur_user: dict = Depends(require_planer)):
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
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")

    # Collect source entries grouped by date
    # We query each date individually via the schedule tables
    from sp5lib.dbf_reader import get_table_fields
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
