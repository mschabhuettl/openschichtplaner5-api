"""Absences, leave entitlements, holiday bans, annual close router."""
import os
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from ..dependencies import (
    get_db, require_admin, require_planer, _sanitize_500,
)
from .events import broadcast
from .notifications import create_notification

router = APIRouter()



@router.delete("/api/absences/{employee_id}/{date}", tags=["Absences"], summary="Delete absence entry")
def delete_absence_only(employee_id: int, date: str, _cur_user: dict = Depends(require_planer)):
    """Delete only absence entries (ABSEN) for an employee on a date, leaving shifts intact."""
    try:
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden")
    try:
        count = get_db().delete_absence_only(employee_id, date)
        broadcast("absence_changed", {"employee_id": employee_id, "date": date})
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Write: absence ───────────────────────────────────────────
class AbsenceCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    leave_type_id: int = Field(..., gt=0)

    @field_validator('date')
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt
        try:
            _dtt.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Datum muss ein gültiges Datum im Format YYYY-MM-DD sein")
        return v


@router.get("/api/absences", tags=["Absences"], summary="List absences", description="Return absence entries, optionally filtered by year, employee, or leave type.")
def list_absences(
    year: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
    leave_type_id: Optional[int] = Query(None),
):
    """List all absences with optional filters."""
    return get_db().get_absences_list(year=year, employee_id=employee_id, leave_type_id=leave_type_id)


@router.get("/api/group-assignments", tags=["Groups"], summary="List group assignments")
def get_all_group_assignments():
    """Return all group assignments (employee_id, group_id pairs)."""
    return get_db().get_all_group_assignments()


@router.post("/api/absences", tags=["Absences"], summary="Create absence", description="Add an absence entry for an employee on a date. Requires Planer role.")
def create_absence(body: AbsenceCreate, _cur_user: dict = Depends(require_planer)):
    # Date validation handled by Pydantic model
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")
    if db.get_leave_type(body.leave_type_id) is None:
        raise HTTPException(status_code=404, detail=f"Abwesenheitstyp {body.leave_type_id} nicht gefunden")
    try:
        result = db.add_absence(body.employee_id, body.date, body.leave_type_id)
        broadcast("absence_changed", {"employee_id": body.employee_id, "date": body.date})
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


# ── Bulk Absence ──────────────────────────────────────────────

class BulkAbsenceCreate(BaseModel):
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$', description="Datum (YYYY-MM-DD)")
    leave_type_id: int = Field(..., gt=0)
    employee_ids: Optional[List[int]] = Field(None, description="Bestimmte MA-IDs; None = alle aktiven MA")

    @field_validator('date')
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt
        try:
            _dtt.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Datum muss ein gültiges Datum im Format YYYY-MM-DD sein")
        return v


@router.post("/api/absences/bulk", tags=["Absences"], summary="Bulk absence: add absence for multiple employees")
def bulk_create_absence(body: BulkAbsenceCreate, _cur_user: dict = Depends(require_planer)):
    """Add an absence entry for multiple employees (or all active) on one date."""
    db = get_db()
    if db.get_leave_type(body.leave_type_id) is None:
        raise HTTPException(status_code=404, detail=f"Abwesenheitstyp {body.leave_type_id} nicht gefunden")

    if body.employee_ids:
        employees = [db.get_employee(eid) for eid in body.employee_ids]
        employees = [e for e in employees if e is not None]
    else:
        employees = db.get_employees(include_hidden=False)

    results = {"ok": True, "created": 0, "skipped": 0, "errors": []}
    for emp in employees:
        try:
            db.add_absence(emp['ID'], body.date, body.leave_type_id)
            broadcast("absence_changed", {"employee_id": emp['ID'], "date": body.date})
            results["created"] += 1
        except ValueError:
            # Already exists – skip silently
            results["skipped"] += 1
        except Exception as e:
            import logging as _logging
            _logging.getLogger('sp5api').error(
                "bulk_create_absence emp_id=%s error=%s", emp['ID'], str(e)
            )
            results["errors"].append({"id": emp['ID'], "error": "Interner Fehler beim Speichern"})

    return results


# ── Leave Entitlements ────────────────────────────────────────

@router.get("/api/leave-entitlements", tags=["Absences"], summary="List vacation entitlements")
def get_leave_entitlements(
    year: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
):
    return get_db().get_leave_entitlements(year=year, employee_id=employee_id)


class LeaveEntitlementCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    year: int = Field(..., ge=2000, le=2100, description="Urlaubsjahr")
    days: float = Field(..., ge=0, le=366, description="Urlaubsanspruch in Tagen")
    carry_forward: Optional[float] = Field(0, ge=0, le=366)
    leave_type_id: Optional[int] = Field(0, ge=0)


@router.post("/api/leave-entitlements", tags=["Absences"], summary="Set vacation entitlement")
def set_leave_entitlement(body: LeaveEntitlementCreate, _cur_user: dict = Depends(require_planer)):
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
        raise _sanitize_500(e)


@router.get("/api/leave-balance", tags=["Absences"], summary="Get employee leave balance")
def get_leave_balance(
    year: int = Query(...),
    employee_id: int = Query(...),
):
    return get_db().get_leave_balance(employee_id=employee_id, year=year)


@router.get("/api/leave-balance/group", tags=["Absences"], summary="Get group leave balance")
def get_leave_balance_group(
    year: int = Query(...),
    group_id: int = Query(...),
):
    return get_db().get_leave_balance_group(year=year, group_id=group_id)


# ── Holiday Bans ──────────────────────────────────────────────

@router.get("/api/holiday-bans", tags=["Absences"], summary="List holiday ban periods")
def get_holiday_bans(
    group_id: Optional[int] = Query(None),
):
    return get_db().get_holiday_bans(group_id=group_id)


class HolidayBanCreate(BaseModel):
    group_id: int = Field(..., gt=0)
    start_date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    reason: Optional[str] = Field('', max_length=500)

    @field_validator('start_date', 'end_date')
    @classmethod
    def validate_dates(cls, v: str) -> str:
        from datetime import datetime as _dtt
        try:
            _dtt.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Datum muss ein gültiges Datum im Format YYYY-MM-DD sein")
        return v

    @model_validator(mode='after')
    def end_after_start(self) -> 'HolidayBanCreate':
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date muss >= start_date sein")
        return self


@router.post("/api/holiday-bans", tags=["Absences"], summary="Create holiday ban period")
def create_holiday_ban(body: HolidayBanCreate, _cur_user: dict = Depends(require_planer)):
    # Date validation and range check handled by Pydantic model
    try:
        result = get_db().create_holiday_ban(
            group_id=body.group_id,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason or '',
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/holiday-bans/{ban_id}", tags=["Absences"], summary="Delete holiday ban period")
def delete_holiday_ban(ban_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_holiday_ban(ban_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Annual Close ──────────────────────────────────────────────

@router.get("/api/annual-close/preview", tags=["Absences"], summary="Preview annual close (Jahresabschluss)")
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


@router.post("/api/annual-close", tags=["Absences"], summary="Execute annual close")
def run_annual_close(body: AnnualCloseBody, _cur_user: dict = Depends(require_admin)):
    try:
        result = get_db().run_annual_close(
            year=body.year,
            group_id=body.group_id,
            carry_forward_days=body.max_carry_forward_days or 10,
        )
        return {"ok": True, **result}
    except Exception as e:
        raise _sanitize_500(e)


# ── Absence Status (approval workflow) ───────────────────────────────────────

import json as _json  # noqa: E402

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


@router.get("/api/absences/status", tags=["Absences"], summary="List absence approval status")
def get_all_absence_statuses():
    """Return the status dict for all absences (id → {status, reject_reason}).
    Also supports legacy format (id → status string) and normalizes on read."""
    raw = _load_absence_status()
    # Normalize: legacy entries may be plain strings
    normalized: dict = {}
    for k, v in raw.items():
        if isinstance(v, str):
            normalized[k] = {"status": v, "reject_reason": ""}
        else:
            normalized[k] = v
    return normalized


class AbsenceStatusPatch(BaseModel):
    status: str  # 'pending' | 'approved' | 'rejected'
    reject_reason: Optional[str] = Field(None, max_length=500)


@router.patch("/api/absences/{absence_id}/status", tags=["Absences"], summary="Update absence approval status")
def patch_absence_status(absence_id: int, body: AbsenceStatusPatch, _cur_user: dict = Depends(require_planer)):
    """Update approval status for an absence record."""
    allowed = {'pending', 'approved', 'rejected'}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {allowed}")
    data = _load_absence_status()
    entry: dict = {"status": body.status, "reject_reason": ""}
    if body.status == 'rejected' and body.reject_reason:
        entry["reject_reason"] = body.reject_reason.strip()
    # Preserve existing reject_reason when approving/resetting (optional)
    old = data.get(str(absence_id))
    if isinstance(old, dict) and body.status != 'rejected':
        entry["reject_reason"] = ""  # clear reason when not rejected
    data[str(absence_id)] = entry
    _save_absence_status(data)

    # ── Notification trigger: inform employee about status change ──
    if body.status in ('approved', 'rejected'):
        try:
            # Look up the absence to find the employee
            all_absences = get_db().get_absences()
            absence = next((a for a in all_absences if a.get('ID') == absence_id), None)
            if absence:
                emp_id = absence.get('MitarbeiterID') or absence.get('employee_id')
                _emp_name = absence.get('MitarbeiterName', f'MA #{emp_id}')
                date_str = absence.get('Datum') or absence.get('date', '')
                if body.status == 'approved':
                    title = '✅ Urlaubsantrag genehmigt'
                    message = f'Dein Urlaubsantrag für {date_str} wurde genehmigt.'
                else:
                    reason = entry.get('reject_reason', '')
                    title = '❌ Urlaubsantrag abgelehnt'
                    message = f'Dein Urlaubsantrag für {date_str} wurde abgelehnt.' + (f' Grund: {reason}' if reason else '')
                create_notification(
                    type='absence_status',
                    title=title,
                    message=message,
                    recipient_employee_id=emp_id,
                    link='/urlaub',
                )
        except Exception:
            pass  # Never fail the main request due to notification issues

    return {"ok": True, "id": absence_id, **entry}
