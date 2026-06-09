"""Absences, leave entitlements, holiday bans, annual close router."""

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from ..dependencies import (
    _sanitize_500,
    get_db,
    require_admin,
    require_planer,
    require_role,
)
from ..schemas import paginate
from .events import broadcast
from .notifications import create_notification

router = APIRouter()


@router.delete(
    "/api/absences/{employee_id}/{date}",
    tags=["Absences"],
    summary="Delete absence entry",
    description="Remove the absence record for an employee on a specific date. Shifts on that day are preserved. Requires Planer role.",
)
def delete_absence_only(
    employee_id: int, date: str, _cur_user: dict = Depends(require_planer)
):
    """Delete only absence entries (ABSEN) for an employee on a date, leaving shifts intact."""
    try:
        from datetime import datetime

        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format, please use YYYY-MM-DD",
        )
    try:
        db = get_db()
        count = db.delete_absence_only(employee_id, date)
        broadcast("absence_changed", {"employee_id": employee_id, "date": date})
        # Audit: absence deleted
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="DELETE",
            entity="absence",
            entity_id=employee_id,
            details=f"Absence for employee {employee_id} on {date} deleted",
            old_value={"employee_id": employee_id, "date": date},
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Write: absence ───────────────────────────────────────────
class AbsenceCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    leave_type_id: int = Field(..., gt=0)

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt

        try:
            _dtt.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be a valid date in YYYY-MM-DD format")
        return v


@router.get(
    "/api/absences",
    tags=["Absences"],
    summary="List absences",
    description="Return absence entries, optionally filtered by year, employee, or leave type.",
)
def list_absences(
    year: int | None = Query(None),
    employee_id: int | None = Query(None),
    leave_type_id: int | None = Query(None),
    page: int | None = Query(None, ge=1, description="Page number (1-based). Omit for unpaginated list."),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
):
    """List all absences with optional filters."""
    result = get_db().get_absences_list(
        year=year, employee_id=employee_id, leave_type_id=leave_type_id
    )
    return paginate(result, page, page_size)


@router.get("/api/group-assignments", tags=["Groups"], summary="List group assignments", description="Return all group assignments (employee_id, group_id pairs).")
def get_all_group_assignments():
    """Return all group assignments (employee_id, group_id pairs)."""
    return get_db().get_all_group_assignments()


@router.post(
    "/api/absences",
    tags=["Absences"],
    summary="Create absence",
    description="Add an absence entry for an employee on a date. Requires Planer role.",
)
def create_absence(body: AbsenceCreate, _cur_user: dict = Depends(require_planer)):
    # Date validation handled by Pydantic model
    db = get_db()
    if db.get_employee(body.employee_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden"
        )
    if db.get_leave_type(body.leave_type_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Abwesenheitstyp {body.leave_type_id} nicht gefunden",
        )

    # ── Conflict & holiday warnings ──────────────────────────────
    warnings: list[str] = []
    try:
        year = int(body.date[:4])
        # Check for existing shift assignment on this date
        day_entries = db.get_schedule_day(body.date)
        emp_entry = next(
            (
                e
                for e in day_entries
                if e.get("employee_id") == body.employee_id
                and e.get("kind") in ("shift", "special_shift")
            ),
            None,
        )
        if emp_entry:
            shift_name = (
                emp_entry.get("shift_name") or emp_entry.get("custom_name") or "Schicht"
            )
            warnings.append(
                f"Mitarbeiter hat an diesem Tag bereits eine Schicht ({shift_name})."
            )
        # Check if date is a public holiday
        holiday_dates = db.get_holiday_dates(year)
        if body.date in holiday_dates:
            warnings.append(
                "Dieses Datum ist ein Feiertag – der Urlaub wird trotzdem vom Kontingent abgezogen."
            )
    except Exception:
        pass  # Never block creation due to warning check errors

    try:
        result = db.add_absence(body.employee_id, body.date, body.leave_type_id)
        broadcast(
            "absence_changed", {"employee_id": body.employee_id, "date": body.date}
        )

        # ── Auto-set status to "pending" for approval workflow ────────────
        absence_id = result.get("ID")
        if absence_id:
            try:
                status_data = _load_absence_status()
                status_data[str(absence_id)] = {"status": "pending", "reject_reason": ""}
                _save_absence_status(status_data)
            except Exception:
                pass  # Never block creation due to status file errors

            # ── Notify planners about the new request ─────────────────────
            try:
                emp = db.get_employee(body.employee_id)
                emp_name = f"{emp.get('NAME', '')} {emp.get('FIRSTNAME', '')}".strip() if emp else f"MA #{body.employee_id}"
                lt = db.get_leave_type(body.leave_type_id)
                lt_name = lt.get("NAME", f"Typ #{body.leave_type_id}") if lt else f"Typ #{body.leave_type_id}"
                create_notification(
                    type="vacation_request",
                    title="Neuer Urlaubsantrag",
                    message=f"{emp_name}: {lt_name} am {body.date}",
                    recipient_employee_id=None,  # None = planner-wide notification
                    link="/urlaub",
                )
            except Exception:
                pass  # Never block creation due to notification errors

        # Audit: absence created
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="CREATE",
            entity="absence",
            entity_id=body.employee_id,
            details=f"Absence type {body.leave_type_id} for employee {body.employee_id} on {body.date}",
            new_value={"employee_id": body.employee_id, "date": body.date, "leave_type_id": body.leave_type_id},
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "record": result, "warnings": warnings}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


# ── Bulk Absence ──────────────────────────────────────────────


class BulkAbsenceCreate(BaseModel):
    date: str = Field(
        ..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Datum (YYYY-MM-DD)"
    )
    leave_type_id: int = Field(..., gt=0)
    employee_ids: list[int] | None = Field(
        None, description="Bestimmte MA-IDs; None = alle aktiven MA"
    )

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt

        try:
            _dtt.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be a valid date in YYYY-MM-DD format")
        return v


@router.post(
    "/api/absences/bulk",
    tags=["Absences"],
    summary="Bulk absence: add absence for multiple employees",
    description="Add the same absence type for multiple (or all active) employees on one date in a single request. Requires Planer role.",
)
def bulk_create_absence(
    body: BulkAbsenceCreate, _cur_user: dict = Depends(require_planer)
):
    """Add an absence entry for multiple employees (or all active) on one date."""
    db = get_db()
    if db.get_leave_type(body.leave_type_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Abwesenheitstyp {body.leave_type_id} nicht gefunden",
        )

    if body.employee_ids:
        # Build a map once instead of calling get_employee() per ID (O(N) vs O(N*M))
        all_emp_map: dict[int, dict] = {
            e["ID"]: e for e in db.get_employees(include_hidden=True)
        }
        employees: list[dict] = [
            all_emp_map[eid] for eid in body.employee_ids if eid in all_emp_map
        ]
    else:
        employees = db.get_employees(include_hidden=False)

    results: dict[str, Any] = {"ok": True, "created": 0, "skipped": 0, "errors": []}
    created_ids: list[int] = []
    for emp in employees:
        try:
            rec = db.add_absence(emp["ID"], body.date, body.leave_type_id)
            broadcast("absence_changed", {"employee_id": emp["ID"], "date": body.date})
            results["created"] += 1
            if rec and rec.get("ID"):
                created_ids.append(rec["ID"])
        except ValueError:
            # Already exists – skip silently
            results["skipped"] += 1
        except Exception as e:
            import logging as _logging

            _logging.getLogger("sp5api").error(
                "bulk_create_absence emp_id=%s error=%s", emp["ID"], str(e)
            )
            results["errors"].append(
                {"id": emp["ID"], "error": "Interner Fehler beim Speichern"}
            )

    # ── Auto-set status to "pending" for all created absences ─────────────
    if created_ids:
        try:
            status_data = _load_absence_status()
            for aid in created_ids:
                status_data[str(aid)] = {"status": "pending", "reject_reason": ""}
            _save_absence_status(status_data)
        except Exception:
            pass

        # ── Notify planners about bulk request ────────────────────────────
        try:
            lt = db.get_leave_type(body.leave_type_id)
            lt_name = lt.get("NAME", f"Typ #{body.leave_type_id}") if lt else f"Typ #{body.leave_type_id}"
            create_notification(
                type="vacation_request",
                title="Neue Urlaubsanträge (Sammel)",
                message=f"{len(created_ids)} Anträge: {lt_name} am {body.date}",
                recipient_employee_id=None,
                link="/urlaub",
            )
        except Exception:
            pass

    return results


# ── Leave Entitlements ────────────────────────────────────────


@router.get(
    "/api/leave-entitlements", tags=["Absences"], summary="List vacation entitlements",
    description="Return vacation/leave entitlements for a given year, optionally filtered by employee.",
)
def get_leave_entitlements(
    year: int | None = Query(None),
    employee_id: int | None = Query(None),
):
    return get_db().get_leave_entitlements(year=year, employee_id=employee_id)


class LeaveEntitlementCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    year: int = Field(..., ge=2000, le=2100, description="Urlaubsjahr")
    days: float = Field(..., ge=0, le=366, description="Urlaubsanspruch in Tagen")
    carry_forward: float | None = Field(0, ge=0, le=366)
    leave_type_id: int | None = Field(0, ge=0)


@router.post(
    "/api/leave-entitlements", tags=["Absences"], summary="Set vacation entitlement",
    description="Set the annual leave entitlement for an employee. Requires Planer role.",
)
def set_leave_entitlement(
    body: LeaveEntitlementCreate, _cur_user: dict = Depends(require_planer)
):
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


@router.get(
    "/api/leave-balance", tags=["Absences"], summary="Get employee leave balance",
    description="Return the leave balance (entitlement vs. used) for a specific employee and year.",
)
def get_leave_balance(
    year: int = Query(...),
    employee_id: int = Query(...),
):
    return get_db().get_leave_balance(employee_id=employee_id, year=year)


@router.get(
    "/api/leave-balance/group", tags=["Absences"], summary="Get group leave balance",
    description="Return leave balances for all employees in a group for a given year.",
)
def get_leave_balance_group(
    year: int = Query(...),
    group_id: int = Query(...),
):
    return get_db().get_leave_balance_group(year=year, group_id=group_id)


# ── Holiday Bans ──────────────────────────────────────────────


@router.get("/api/holiday-bans", tags=["Absences"], summary="List holiday ban periods", description="Return all holiday ban periods (Urlaubssperren).")
def get_holiday_bans(
    group_id: int | None = Query(None),
):
    return get_db().get_holiday_bans(group_id=group_id)


class HolidayBanCreate(BaseModel):
    group_id: int = Field(..., gt=0)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: str | None = Field("", max_length=500)

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_dates(cls, v: str) -> str:
        from datetime import datetime as _dtt

        try:
            _dtt.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date must be a valid date in YYYY-MM-DD format")
        return v

    @model_validator(mode="after")
    def end_after_start(self) -> "HolidayBanCreate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date muss >= start_date sein")
        return self


@router.post(
    "/api/holiday-bans", tags=["Absences"], summary="Create holiday ban period"
)
def create_holiday_ban(
    body: HolidayBanCreate, _cur_user: dict = Depends(require_planer)
):
    # Date validation and range check handled by Pydantic model
    try:
        result = get_db().create_holiday_ban(
            group_id=body.group_id,
            start_date=body.start_date,
            end_date=body.end_date,
            reason=body.reason or "",
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/holiday-bans/{ban_id}", tags=["Absences"], summary="Delete holiday ban period",
    description="Delete a holiday ban period by ID. Requires Planer role.",
)
def delete_holiday_ban(ban_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_holiday_ban(ban_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Annual Close ──────────────────────────────────────────────


@router.get(
    "/api/annual-close/preview",
    tags=["Absences"],
    summary="Preview annual close (Jahresabschluss)",
    description="Preview the annual closing calculation without applying changes.",
)
def annual_close_preview(
    year: int = Query(...),
    group_id: int | None = Query(None),
    max_carry_forward_days: float = Query(10),
):
    return get_db().get_annual_close_preview(
        year=year,
        group_id=group_id,
        carry_forward_days=max_carry_forward_days,
    )


class AnnualCloseBody(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    group_id: int | None = Field(None, gt=0)
    max_carry_forward_days: float | None = Field(10, ge=0, le=366)


@router.post("/api/annual-close", tags=["Absences"], summary="Execute annual close", description="Execute the annual closing (Jahresabschluss) — carry forward balances to next year. Requires Admin role.")
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

_STATUS_FILE = os.path.join(os.path.dirname(__file__), "..", "absence_status.json")


def _load_absence_status() -> dict:
    try:
        if os.path.exists(_STATUS_FILE):
            with open(_STATUS_FILE, encoding="utf-8") as f:
                return _json.load(f)
    except Exception:
        pass
    return {}


def _save_absence_status(data: dict) -> None:
    try:
        with open(_STATUS_FILE, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2)
    except Exception:
        pass


@router.get(
    "/api/absences/status", tags=["Absences"], summary="List absence approval status",
    description="Return the status dict for all absences (id → {status, reject_reason}). Also supports legacy format (id → status string) and normalizes on read.",
)
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
    status: str = Field(..., pattern=r"^(pending|approved|rejected)$")
    reject_reason: str | None = Field(None, max_length=500)


@router.patch(
    "/api/absences/{absence_id}/status",
    tags=["Absences"],
    summary="Update absence approval status",
    description="Approve or reject an absence request. Status must be one of: `pending`, `approved`, `rejected`. Rejection reason is required when rejecting. Requires Planer role.",
)
def patch_absence_status(
    absence_id: int, body: AbsenceStatusPatch, _cur_user: dict = Depends(require_planer)
):
    """Update approval status for an absence record."""
    allowed = {"pending", "approved", "rejected"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {allowed}")
    data = _load_absence_status()
    entry: dict = {"status": body.status, "reject_reason": ""}
    if body.status == "rejected" and body.reject_reason:
        entry["reject_reason"] = body.reject_reason.strip()
    # Preserve existing reject_reason when approving/resetting (optional)
    old = data.get(str(absence_id))
    if isinstance(old, dict) and body.status != "rejected":
        entry["reject_reason"] = ""  # clear reason when not rejected
    data[str(absence_id)] = entry
    _save_absence_status(data)

    # ── Look up absence record BEFORE potential deletion ──────────────────────
    # get_absences_list returns lowercase keys: id, employee_id, date
    _absence_rec: dict = {}
    try:
        all_absences = get_db().get_absences_list()
        found = next((a for a in all_absences if a.get("id") == absence_id), None)
        if found:
            _absence_rec = found
    except Exception:
        pass

    # ── When rejected: remove from ABSEN table so employee is no longer marked absent ──
    rejected_removed = False
    if body.status == "rejected" and _absence_rec:
        try:
            emp_id_del = _absence_rec.get("employee_id")
            date_del = _absence_rec.get("date", "")
            if emp_id_del and date_del:
                get_db().delete_absence_only(emp_id_del, date_del)
                broadcast(
                    "absence_changed", {"employee_id": emp_id_del, "date": date_del}
                )
                rejected_removed = True
        except Exception:
            pass  # Never fail the main request due to cleanup errors

    # ── Notification trigger: inform employee about status change ──
    if body.status in ("approved", "rejected") and _absence_rec:
        try:
            emp_id = _absence_rec.get("employee_id")
            date_str = _absence_rec.get("date", "")
            if emp_id:
                if body.status == "approved":
                    title = "✅ Urlaubsantrag genehmigt"
                    message = f"Dein Urlaubsantrag für {date_str} wurde genehmigt."
                else:
                    reason = entry.get("reject_reason", "")
                    title = "❌ Urlaubsantrag abgelehnt"
                    message = f"Dein Urlaubsantrag für {date_str} wurde abgelehnt." + (
                        f" Grund: {reason}" if reason else ""
                    )
                create_notification(
                    type="absence_status",
                    title=title,
                    message=message,
                    recipient_employee_id=emp_id,
                    link="/urlaub",
                )
        except Exception:
            pass  # Never fail the main request due to notification issues

    return {"ok": True, "id": absence_id, "rejected_removed": rejected_removed, **entry}


# ── Absence Statistics ────────────────────────────────────────────────────────

def _classify_leave_type(lt: dict | None) -> str:
    """Classify a leave type into 'vacation', 'sick', or 'other'."""
    if lt is None:
        return "other"
    # Vacation: ENTITLED flag (counts against leave quota)
    if lt.get("ENTITLED"):
        return "vacation"
    # Sick: detect by name/shortname keyword
    lt_name = (lt.get("NAME", "") or "").lower()
    lt_short = (lt.get("SHORTNAME", "") or "").lower()
    if any(kw in lt_name or kw in lt_short for kw in ["krank", "sick", "ku"]):
        return "sick"
    return "other"


def _build_employee_stats(
    employee_id: int, year: int, absences: list[dict], lt_map: dict, status_data: dict
) -> dict:
    """Build per-employee absence stats for a given year."""
    year_str = str(year)
    vacation_days = 0
    sick_days = 0
    other_days = 0
    by_month: dict[int, dict] = {m: {"month": m, "vacation": 0, "sick": 0, "other": 0} for m in range(1, 13)}
    pending_requests = 0

    for ab in absences:
        if ab.get("employee_id") != employee_id:
            continue
        d = ab.get("date", "")
        if not d.startswith(year_str):
            continue
        lt_id = ab.get("leave_type_id")
        lt = lt_map.get(lt_id) if lt_id else None
        category = _classify_leave_type(lt)
        try:
            month = int(d[5:7])
        except (ValueError, IndexError):
            continue
        if category == "vacation":
            vacation_days += 1
            by_month[month]["vacation"] += 1
        elif category == "sick":
            sick_days += 1
            by_month[month]["sick"] += 1
        else:
            other_days += 1
            by_month[month]["other"] += 1

        # Count pending: check absence_status.json by id
        ab_id = ab.get("id")
        if ab_id is not None:
            entry = status_data.get(str(ab_id))
            if isinstance(entry, dict):
                if entry.get("status") == "pending":
                    pending_requests += 1
            elif isinstance(entry, str) and entry == "pending":
                pending_requests += 1

    return {
        "employee_id": employee_id,
        "year": year,
        "vacation_days": vacation_days,
        "sick_days": sick_days,
        "other_days": other_days,
        "total_days": vacation_days + sick_days + other_days,
        "by_month": list(by_month.values()),
        "pending_requests": pending_requests,
    }


@router.get(
    "/api/absences/stats/employee/{employee_id}",
    tags=["Absence Statistics"],
    summary="Absence statistics for an employee",
    description=(
        "Return vacation, sick, and other absence counts for a specific employee in a year. "
        "Requires at least Planer role."
    ),
)
def get_absence_stats_employee(
    employee_id: int,
    year: int = Query(..., ge=2000, le=2100, description="Year (YYYY)"),
    _cur_user: dict = Depends(require_role("Planer")),
):
    """Return absence statistics for one employee in a year."""
    try:
        db = get_db()
        emp = db.get_employee(employee_id)
        if emp is None:
            raise HTTPException(status_code=404, detail=f"Mitarbeiter {employee_id} nicht gefunden")
        absences = db.get_absences_list(year=year, employee_id=employee_id)
        lt_map = {lt["ID"]: lt for lt in db.get_leave_types(include_hidden=True)}
        status_data = _load_absence_status()
        stats = _build_employee_stats(employee_id, year, absences, lt_map, status_data)
        stats["employee_name"] = f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", ")
        return stats
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.get(
    "/api/absences/stats/group/{group_id}",
    tags=["Absence Statistics"],
    summary="Absence statistics for a group",
    description=(
        "Return per-employee absence stats plus group totals and top-3 rankings for a group in a year. "
        "Requires at least Planer role."
    ),
)
def get_absence_stats_group(
    group_id: int,
    year: int = Query(..., ge=2000, le=2100, description="Year (YYYY)"),
    _cur_user: dict = Depends(require_role("Planer")),
):
    """Return group-level absence stats: per-employee breakdown, totals, top-3."""
    try:
        db = get_db()
        groups_map = {g["ID"]: g for g in db.get_groups(include_hidden=True)}
        if group_id not in groups_map:
            raise HTTPException(status_code=404, detail=f"Gruppe {group_id} nicht gefunden")
        group = groups_map[group_id]

        member_ids = db.get_group_members(group_id)
        emp_map = {e["ID"]: e for e in db.get_employees(include_hidden=True)}
        lt_map = {lt["ID"]: lt for lt in db.get_leave_types(include_hidden=True)}
        absences = db.get_absences_list(year=year)
        status_data = _load_absence_status()

        employees_stats = []
        group_vacation = 0
        group_sick = 0
        group_other = 0

        for eid in member_ids:
            emp = emp_map.get(eid)
            if not emp:
                continue
            stats = _build_employee_stats(eid, year, absences, lt_map, status_data)
            stats["employee_name"] = f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", ")
            employees_stats.append(stats)
            group_vacation += stats["vacation_days"]
            group_sick += stats["sick_days"]
            group_other += stats["other_days"]

        employees_stats.sort(key=lambda x: x.get("employee_name", ""))

        top3_sick = sorted(employees_stats, key=lambda x: -x["sick_days"])[:3]
        top3_vacation = sorted(employees_stats, key=lambda x: -x["vacation_days"])[:3]

        return {
            "group_id": group_id,
            "group_name": group.get("NAME", ""),
            "year": year,
            "employees": employees_stats,
            "group_totals": {
                "vacation_days": group_vacation,
                "sick_days": group_sick,
                "other_days": group_other,
                "total_days": group_vacation + group_sick + group_other,
            },
            "top3_by_sick_days": [
                {"employee_id": s["employee_id"], "employee_name": s["employee_name"], "sick_days": s["sick_days"]}
                for s in top3_sick
            ],
            "top3_by_vacation_days": [
                {"employee_id": s["employee_id"], "employee_name": s["employee_name"], "vacation_days": s["vacation_days"]}
                for s in top3_vacation
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.get(
    "/api/absences/stats/overview",
    tags=["Absence Statistics"],
    summary="Company-wide absence statistics overview",
    description=(
        "Return company-wide absence stats: all groups with totals and month-by-month breakdown. "
        "Requires at least Planer role."
    ),
)
def get_absence_stats_overview(
    year: int = Query(..., ge=2000, le=2100, description="Year (YYYY)"),
    _cur_user: dict = Depends(require_role("Planer")),
):
    """Return company-wide absence stats: groups summary + monthly breakdown."""
    try:
        db = get_db()
        all_groups = db.get_groups(include_hidden=False)
        lt_map = {lt["ID"]: lt for lt in db.get_leave_types(include_hidden=True)}
        absences = db.get_absences_list(year=year)
        status_data = _load_absence_status()

        # Monthly company-wide totals
        monthly: dict[int, dict] = {
            m: {"month": m, "vacation": 0, "sick": 0, "other": 0} for m in range(1, 13)
        }
        year_str = str(year)

        # Pre-compute all absences with category
        for ab in absences:
            d = ab.get("date", "")
            if not d.startswith(year_str):
                continue
            lt_id = ab.get("leave_type_id")
            lt = lt_map.get(lt_id) if lt_id else None
            category = _classify_leave_type(lt)
            try:
                month = int(d[5:7])
            except (ValueError, IndexError):
                continue
            monthly[month][category] += 1

        groups_summary = []
        for grp in all_groups:
            gid = grp["ID"]
            member_ids = db.get_group_members(gid)
            grp_vacation = 0
            grp_sick = 0
            grp_other = 0
            for eid in member_ids:
                stats = _build_employee_stats(eid, year, absences, lt_map, status_data)
                grp_vacation += stats["vacation_days"]
                grp_sick += stats["sick_days"]
                grp_other += stats["other_days"]
            groups_summary.append({
                "group_id": gid,
                "group_name": grp.get("NAME", ""),
                "vacation_days": grp_vacation,
                "sick_days": grp_sick,
                "other_days": grp_other,
                "total_days": grp_vacation + grp_sick + grp_other,
            })

        total_vacation = sum(g["vacation_days"] for g in groups_summary)
        total_sick = sum(g["sick_days"] for g in groups_summary)
        total_other = sum(g["other_days"] for g in groups_summary)

        return {
            "year": year,
            "company_totals": {
                "vacation_days": total_vacation,
                "sick_days": total_sick,
                "other_days": total_other,
                "total_days": total_vacation + total_sick + total_other,
            },
            "groups": groups_summary,
            "by_month": list(monthly.values()),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)
