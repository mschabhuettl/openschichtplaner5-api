"""Master data router: shifts, leave-types, workplaces, holidays, extracharges, staffing-requirements, skills."""


from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import cache
from ..dependencies import (
    _logger,
    _sanitize_500,
    get_db,
    require_admin,
    require_auth,
    require_planer,
)
from ..schemas import ShiftResponse

router = APIRouter()


@router.get(
    "/api/shifts",
    tags=["Shifts"],
    summary="List shifts",
    description="Return all shift definitions. Set include_hidden=true to include archived shifts.",
    response_model=list[ShiftResponse],
)
def get_shifts(include_hidden: bool = False):
    cache_key = f"shifts:list:{include_hidden}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = get_db().get_shifts(include_hidden=include_hidden)
    cache.put(cache_key, result)
    return result


@router.get("/api/leave-types", tags=["Absences"], summary="List leave types", description="Return all configured leave/absence types.")
def get_leave_types(include_hidden: bool = False):
    cache_key = f"leave_types:list:{include_hidden}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = get_db().get_leave_types(include_hidden=include_hidden)
    cache.put(cache_key, result)
    return result


@router.get("/api/workplaces", tags=["Employees"], summary="List workplaces", description="Return all configured workplaces/locations.")
def get_workplaces(include_hidden: bool = False):
    cache_key = f"workplaces:list:{include_hidden}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = get_db().get_workplaces(include_hidden=include_hidden)
    cache.put(cache_key, result)
    return result


@router.get("/api/holidays", tags=["Events"], summary="List holidays", description="Return all public holidays for a given year.")
def get_holidays(year: int | None = None):
    cache_key = f"holidays:list:{year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = get_db().get_holidays(year=year)
    cache.put(cache_key, result)
    return result


# ── Staffing Requirements ─────────────────────────────────────


@router.get(
    "/api/staffing-requirements",
    tags=["Schedule"],
    summary="List staffing requirements",
    description="Return staffing requirements (min/max headcount per shift/day).",
)
def get_staffing_requirements(
    year: int | None = Query(None),
    month: int | None = Query(None),
    group_id: int | None = Query(None, description="Filter by group ID"),
):
    data = get_db().get_staffing_requirements(year=year, month=month)
    if group_id is not None:
        data["shift_requirements"] = [
            r
            for r in data["shift_requirements"]
            if r.get("group_id") is None or r.get("group_id") == group_id
        ]
    return data


# ── Staffing Requirements Write ──────────────────────────────


class StaffingRequirementSet(BaseModel):
    shift_id: int = Field(..., gt=0)
    weekday: int = Field(..., ge=0, le=6)
    min: int = Field(..., ge=0, le=1000)
    max: int = Field(..., ge=0, le=1000)
    group_id: int = Field(..., gt=0)

    @model_validator(mode="after")
    def max_gte_min(self) -> "StaffingRequirementSet":
        if self.max < self.min:
            raise ValueError("max muss >= min sein")
        return self


@router.post(
    "/api/staffing-requirements", tags=["Schedule"], summary="Set staffing requirement",
    description="Create or update a staffing requirement entry. Requires Admin role.",
)
def set_staffing_requirement(
    body: StaffingRequirementSet, _cur_user: dict = Depends(require_planer)
):
    # All validation handled by Pydantic model (weekday, min, max, group_id, shift_id)
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
        raise _sanitize_500(e)


# ── Write: Shifts ─────────────────────────────────────────────


class ShiftCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    SHORTNAME: str = Field("", max_length=20)
    COLORBK: int = Field(16777215, ge=0, le=16777215)
    COLORTEXT: int = Field(0, ge=0, le=16777215)
    COLORBAR: int = Field(0, ge=0, le=16777215)
    DURATION0: float = Field(0.0, ge=0.0, le=24.0)
    DURATION1: float | None = None
    DURATION2: float | None = None
    DURATION3: float | None = None
    DURATION4: float | None = None
    DURATION5: float | None = None
    DURATION6: float | None = None
    DURATION7: float | None = None
    STARTEND0: str | None = None
    STARTEND1: str | None = None
    STARTEND2: str | None = None
    STARTEND3: str | None = None
    STARTEND4: str | None = None
    STARTEND5: str | None = None
    STARTEND6: str | None = None
    STARTEND7: str | None = None
    HIDE: bool = False


class ShiftUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    SHORTNAME: str | None = Field(None, max_length=20)
    COLORBK: int | None = Field(None, ge=0, le=16777215)
    COLORTEXT: int | None = Field(None, ge=0, le=16777215)
    COLORBAR: int | None = Field(None, ge=0, le=16777215)
    DURATION0: float | None = Field(None, ge=0.0, le=24.0)
    DURATION1: float | None = Field(None, ge=0.0, le=24.0)
    DURATION2: float | None = Field(None, ge=0.0, le=24.0)
    DURATION3: float | None = Field(None, ge=0.0, le=24.0)
    DURATION4: float | None = Field(None, ge=0.0, le=24.0)
    DURATION5: float | None = Field(None, ge=0.0, le=24.0)
    DURATION6: float | None = Field(None, ge=0.0, le=24.0)
    DURATION7: float | None = Field(None, ge=0.0, le=24.0)
    STARTEND0: str | None = Field(None, max_length=20)
    STARTEND1: str | None = Field(None, max_length=20)
    STARTEND2: str | None = Field(None, max_length=20)
    STARTEND3: str | None = Field(None, max_length=20)
    STARTEND4: str | None = Field(None, max_length=20)
    STARTEND5: str | None = Field(None, max_length=20)
    STARTEND6: str | None = Field(None, max_length=20)
    STARTEND7: str | None = Field(None, max_length=20)
    POSITION: int | None = None
    HIDE: bool | None = None


@router.post(
    "/api/shifts",
    tags=["Shifts"],
    summary="Create shift",
    description="Create a new shift definition with name, shortname, colors, and time slots per weekday. Requires Admin role.",
)
def create_shift(body: ShiftCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if body.DURATION0 is not None and body.DURATION0 < 0:
        raise HTTPException(
            status_code=400, detail="Feld 'DURATION0' darf nicht negativ sein"
        )
    try:
        result = get_db().create_shift(body.model_dump())
        cache.invalidate("shifts:")
        _logger.warning(
            "AUDIT SHIFT_CREATE | user=%s name=%s id=%s",
            _cur_user.get("NAME"),
            body.NAME,
            result.get("ID"),
        )
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith("DUPLICATE:SHIFTNAME:"):
            raise HTTPException(
                status_code=409,
                detail=f"Schicht mit dem Namen '{body.NAME}' existiert bereits",
            )
        raise _sanitize_500(e, "create_shift")
    except Exception as e:
        raise _sanitize_500(e, "create_shift")


@router.put(
    "/api/shifts/{shift_id}",
    tags=["Shifts"],
    summary="Update shift",
    description="Update an existing shift definition. Only provided fields are changed. Requires Admin role.",
)
def update_shift(
    shift_id: int, body: ShiftUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_shift(shift_id, data)
        cache.invalidate("shifts:")
        _logger.warning(
            "AUDIT SHIFT_UPDATE | user=%s shift_id=%d fields=%s",
            _cur_user.get("NAME"),
            shift_id,
            list(data.keys()),
        )
        return {"ok": True, "record": result}
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Schicht ID {shift_id} nicht gefunden"
        )
    except Exception as e:
        raise _sanitize_500(e, f"update_shift/{shift_id}")


@router.delete(
    "/api/shifts/{shift_id}",
    tags=["Shifts"],
    summary="Delete shift",
    description="Soft-delete (hide) a shift. Use `force=true` to delete even if the shift is still in use. Requires Admin role.",
)
def hide_shift(
    shift_id: int, force: bool = False, _cur_user: dict = Depends(require_admin)
):
    db = get_db()
    if not force:
        usage = db.shift_active_usage_count(shift_id)
        if usage > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Shift {shift_id} is still active in {usage} schedule entries. "
                "Use ?force=true to hide anyway.",
            )
    try:
        count = db.hide_shift(shift_id)
        cache.invalidate("shifts:")
        _logger.warning(
            "AUDIT SHIFT_DELETE | user=%s shift_id=%d force=%s",
            _cur_user.get("NAME"),
            shift_id,
            force,
        )
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e, f"hide_shift/{shift_id}")


# ── Write: Leave Types ────────────────────────────────────────


class LeaveTypeCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    SHORTNAME: str = Field("", max_length=20)
    COLORBK: int = Field(16777215, ge=0, le=16777215)
    COLORTEXT: int = Field(0, ge=0, le=16777215)
    COLORBAR: int = Field(0, ge=0, le=16777215)
    ENTITLED: bool = False
    STDENTIT: float = Field(0.0, ge=0.0, le=366.0)
    HIDE: bool = False


class LeaveTypeUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    SHORTNAME: str | None = Field(None, max_length=20)
    COLORBK: int | None = None
    COLORTEXT: int | None = None
    COLORBAR: int | None = None
    ENTITLED: bool | None = None
    STDENTIT: float | None = None
    POSITION: int | None = None
    HIDE: bool | None = None


@router.post("/api/leave-types", tags=["Absences"], summary="Create leave type", description="Create a new leave/absence type. Requires Admin role.")
def create_leave_type(body: LeaveTypeCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if body.STDENTIT is not None and body.STDENTIT < 0:
        raise HTTPException(
            status_code=400, detail="Feld 'STDENTIT' darf nicht negativ sein"
        )
    try:
        result = get_db().create_leave_type(body.model_dump())
        cache.invalidate("leave_types:")
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, "create_leave_type")


@router.put("/api/leave-types/{lt_id}", tags=["Absences"], summary="Update leave type", description="Update an existing leave/absence type. Requires Admin role.")
def update_leave_type(
    lt_id: int, body: LeaveTypeUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_leave_type(lt_id, data)
        cache.invalidate("leave_types:")
        return {"ok": True, "record": result}
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Abwesenheitstyp ID {lt_id} nicht gefunden"
        )
    except Exception as e:
        raise _sanitize_500(e, f"update_leave_type/{lt_id}")


@router.delete(
    "/api/leave-types/{lt_id}", tags=["Absences"], summary="Delete leave type"
)
def hide_leave_type(
    lt_id: int, force: bool = False, _cur_user: dict = Depends(require_admin)
):
    db = get_db()
    if not force:
        usage = db.leave_type_active_usage_count(lt_id)
        if usage > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Absence type {lt_id} is still active in {usage} absence entries. "
                "Use ?force=true to hide anyway.",
            )
    try:
        count = db.hide_leave_type(lt_id)
        cache.invalidate("leave_types:")
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e, f"hide_leave_type/{lt_id}")


# ── Write: Holidays ───────────────────────────────────────────


class HolidayCreate(BaseModel):
    DATE: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    NAME: str = Field(..., min_length=1, max_length=200)
    INTERVAL: int = 0

    @field_validator("DATE")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import datetime as _dtt

        try:
            _dtt.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("DATE must be a valid date in YYYY-MM-DD format")
        return v


class HolidayUpdate(BaseModel):
    DATE: str | None = None
    NAME: str | None = None
    INTERVAL: int | None = None


@router.post("/api/holidays", tags=["Events"], summary="Create holiday", description="Create a new public holiday entry. Requires Admin role.")
def create_holiday(body: HolidayCreate, _cur_user: dict = Depends(require_admin)):
    # DATE and NAME validation handled by Pydantic model
    try:
        result = get_db().create_holiday(body.model_dump())
        cache.invalidate("holidays:")
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.put("/api/holidays/{holiday_id}", tags=["Events"], summary="Update holiday", description="Update an existing public holiday. Requires Admin role.")
def update_holiday(
    holiday_id: int, body: HolidayUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_holiday(holiday_id, data)
        cache.invalidate("holidays:")
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/holidays/{holiday_id}", tags=["Events"], summary="Delete holiday", description="Delete a public holiday entry. Requires Admin role.")
def delete_holiday(holiday_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_holiday(holiday_id)
        cache.invalidate("holidays:")
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Write: Workplaces ─────────────────────────────────────────


class WorkplaceCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    SHORTNAME: str = Field("", max_length=20)
    COLORBK: int = Field(16777215, ge=0, le=16777215)
    COLORTEXT: int = Field(0, ge=0, le=16777215)
    COLORBAR: int = Field(0, ge=0, le=16777215)
    HIDE: bool = False


class WorkplaceUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    SHORTNAME: str | None = Field(None, max_length=20)
    COLORBK: int | None = None
    COLORTEXT: int | None = None
    COLORBAR: int | None = None
    POSITION: int | None = None
    HIDE: bool | None = None


@router.post("/api/workplaces", tags=["Employees"], summary="Create workplace", description="Create a new workplace/location. Requires Admin role.")
def create_workplace(body: WorkplaceCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        result = get_db().create_workplace(body.model_dump())
        cache.invalidate("workplaces:")
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.put("/api/workplaces/{wp_id}", tags=["Employees"], summary="Update workplace", description="Update an existing workplace/location. Requires Admin role.")
def update_workplace(
    wp_id: int, body: WorkplaceUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_workplace(wp_id, data)
        cache.invalidate("workplaces:")
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/workplaces/{wp_id}", tags=["Employees"], summary="Delete workplace"
)
def hide_workplace(wp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().hide_workplace(wp_id)
        cache.invalidate("workplaces:")
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Workplace ↔ Employee Assignments ──────────────────────────


@router.get(
    "/api/workplaces/{wp_id}/employees",
    tags=["Employees"],
    summary="List workplace employees",
    description="Return employees assigned to a workplace.",
)
def get_workplace_employees(wp_id: int):
    """Return employees assigned to a workplace."""
    try:
        return get_db().get_workplace_employees(wp_id)
    except Exception as e:
        raise _sanitize_500(e)


@router.post(
    "/api/workplaces/{wp_id}/employees/{employee_id}",
    tags=["Employees"],
    summary="Assign employee to workplace",
    description="Assign an employee to a workplace. Requires Admin role.",
)
def assign_employee_to_workplace(
    wp_id: int, employee_id: int, _cur_user: dict = Depends(require_admin)
):
    """Assign an employee to a workplace."""
    try:
        added = get_db().assign_employee_to_workplace(employee_id, wp_id)
        return {"ok": True, "added": added}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/workplaces/{wp_id}/employees/{employee_id}",
    tags=["Employees"],
    summary="Remove employee from workplace",
    description="Remove an employee's workplace assignment. Requires Admin role.",
)
def remove_employee_from_workplace(
    wp_id: int, employee_id: int, _cur_user: dict = Depends(require_admin)
):
    """Remove an employee from a workplace."""
    try:
        removed = get_db().remove_employee_from_workplace(employee_id, wp_id)
        return {"ok": True, "removed": removed}
    except Exception as e:
        raise _sanitize_500(e)


# ── Extra Charges (Time Surcharges) ─────────────────────────────


class ExtraChargeCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    START: int = Field(0, ge=0, le=1440)  # minutes from midnight
    END: int = Field(0, ge=0, le=1440)    # minutes from midnight
    VALIDDAYS: str = Field(
        "0000000",
        min_length=7,
        max_length=7,
        pattern=r"^[01]{7}$",
    )  # 7 chars: 0=inactive, 1=active per weekday (Mon-Sun)
    HOLRULE: int = Field(0, ge=0, le=2)  # 0=no holiday rule, 1=holidays only, 2=not on holidays
    VALIDITY: int = Field(0, ge=0)
    HIDE: bool = False


class ExtraChargeUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    START: int | None = Field(None, ge=0, le=1440)
    END: int | None = Field(None, ge=0, le=1440)
    VALIDDAYS: str | None = Field(None, min_length=7, max_length=7, pattern=r"^[01]{7}$")
    HOLRULE: int | None = Field(None, ge=0, le=2)
    VALIDITY: int | None = Field(None, ge=0)
    POSITION: int | None = None
    HIDE: bool | None = None


@router.get("/api/extracharges", tags=["Statistics"], summary="List extra charges", description="Return all extra charges/surcharges configuration.")
def get_extracharges(include_hidden: bool = False):
    return get_db().get_extracharges(include_hidden=include_hidden)


@router.post("/api/extracharges", tags=["Statistics"], summary="Create extra charge", description="Create a new extra charge entry. Requires Admin role.")
def create_extracharge(
    body: ExtraChargeCreate, _cur_user: dict = Depends(require_admin)
):
    # Validation handled by Pydantic Field constraints
    try:
        result = get_db().create_extracharge(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.get(
    "/api/extracharges/summary", tags=["Statistics"], summary="Extra charges summary",
    description="Return summarized extra charges overview.",
)
def get_extracharges_summary(
    year: int = Query(...),
    month: int = Query(...),
    employee_id: int | None = Query(None),
):
    """Calculate surcharge hours per ExtraCharge rule for a given month."""
    try:
        result = get_db().calculate_extracharge_hours(year, month, employee_id)
        return result
    except Exception as e:
        raise _sanitize_500(e)


@router.put(
    "/api/extracharges/{xc_id}", tags=["Statistics"], summary="Update extra charge",
    description="Update an existing extra charge. Requires Admin role.",
)
def update_extracharge(
    xc_id: int, body: ExtraChargeUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_extracharge(xc_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/extracharges/{xc_id}", tags=["Statistics"], summary="Delete extra charge",
    description="Delete an extra charge entry. Requires Admin role.",
)
def delete_extracharge(xc_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_extracharge(xc_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Special Staffing Requirements (SPDEM) ────────────────────


@router.get(
    "/api/staffing-requirements/special",
    tags=["Schedule"],
    summary="List special staffing requirements",
    description="Return date-specific special staffing requirements.",
)
def get_special_staffing(
    date: str | None = Query(None, description="Date filter YYYY-MM-DD"),
    group_id: int | None = Query(None, description="Group ID filter"),
):
    """Return date-specific staffing requirements from 5SPDEM.DBF."""
    try:
        return get_db().get_special_staffing(date=date, group_id=group_id)
    except Exception as e:
        raise _sanitize_500(e)


class SpecialStaffingCreate(BaseModel):
    group_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    shift_id: int = Field(..., gt=0)
    workplace_id: int = Field(0, ge=0)
    min: int = Field(0, ge=0, le=1000)
    max: int = Field(0, ge=0, le=1000)

    @model_validator(mode="after")
    def max_gte_min(self) -> "SpecialStaffingCreate":
        if self.max < self.min:
            raise ValueError("max muss >= min sein")
        return self


class SpecialStaffingUpdate(BaseModel):
    group_id: int | None = Field(None, gt=0)
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    shift_id: int | None = Field(None, gt=0)
    workplace_id: int | None = Field(None, ge=0)
    min: int | None = Field(None, ge=0, le=1000)
    max: int | None = Field(None, ge=0, le=1000)


@router.post(
    "/api/staffing-requirements/special",
    tags=["Schedule"],
    summary="Create special staffing requirement",
    description="Create a date-specific special staffing requirement. Requires Admin role.",
)
def create_special_staffing(
    body: SpecialStaffingCreate, _cur_user: dict = Depends(require_planer)
):
    """Create a date-specific staffing requirement."""
    # Date format and range validated by Pydantic model
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
        raise _sanitize_500(e)


@router.put(
    "/api/staffing-requirements/special/{record_id}",
    tags=["Schedule"],
    summary="Update special staffing requirement",
    description="Update a special staffing requirement. Requires Admin role.",
)
def update_special_staffing(
    record_id: int,
    body: SpecialStaffingUpdate,
    _cur_user: dict = Depends(require_planer),
):
    """Update a date-specific staffing requirement."""
    data = {k.upper(): v for k, v in body.model_dump().items() if v is not None}
    # Rename keys to match DBF field names
    rename = {
        "GROUP_ID": "GROUPID",
        "SHIFT_ID": "SHIFTID",
        "WORKPLACE_ID": "WORKPLACID",
    }
    data = {rename.get(k, k): v for k, v in data.items()}
    try:
        result = get_db().update_special_staffing(record_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/staffing-requirements/special/{record_id}",
    tags=["Schedule"],
    summary="Delete special staffing requirement",
    description="Delete a date-specific staffing requirement.",
)
def delete_special_staffing(record_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a date-specific staffing requirement."""
    try:
        count = get_db().delete_special_staffing(record_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Datensatz nicht gefunden")
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


# ── Kompetenz-Matrix / Skills ────────────────────────────────────
import json as _json  # noqa: E402
import os  # noqa: E402
import uuid as _uuid  # noqa: E402
from datetime import datetime as _dt  # noqa: E402


def _skills_path() -> str:
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "skills.json")


def _load_skills() -> dict:
    path = _skills_path()
    if not os.path.exists(path):
        return {"skills": [], "assignments": []}
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"skills": [], "assignments": []}


def _save_skills(data: dict):
    with open(_skills_path(), "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field("", max_length=500)
    color: str | None = Field("#3b82f6", max_length=20, pattern=r"^#[0-9a-fA-F]{3,8}$")
    icon: str | None = Field("🎯", max_length=10)
    category: str | None = Field("", max_length=100)


class SkillUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    color: str | None = Field(None, max_length=20, pattern=r"^#[0-9a-fA-F]{3,8}$")
    icon: str | None = Field(None, max_length=10)
    category: str | None = Field(None, max_length=100)


class SkillAssignment(BaseModel):
    employee_id: int = Field(..., gt=0)
    skill_id: str = Field(..., min_length=1, max_length=20)
    level: int | None = Field(1, ge=1, le=3)  # 1=basic, 2=advanced, 3=expert
    certified_until: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # ISO date
    notes: str | None = Field("", max_length=500)


@router.get("/api/skills", tags=["Employees"], summary="List employee skills", description="Return all defined skills/qualifications.")
def get_skills():
    data = _load_skills()
    return data["skills"]


@router.post("/api/skills", tags=["Employees"], summary="Create skill", description="Create a new skill/qualification definition. Requires Admin role.")
def create_skill(body: SkillCreate, _cur_user: dict = Depends(require_admin)):
    data = _load_skills()
    skill = {
        "id": str(_uuid.uuid4())[:8],
        "name": body.name,
        "description": body.description or "",
        "color": body.color or "#3b82f6",
        "icon": body.icon or "🎯",
        "category": body.category or "",
        "created_at": _dt.now().isoformat(timespec="seconds"),
    }
    data["skills"].append(skill)
    _save_skills(data)
    return skill


@router.put("/api/skills/{skill_id}", tags=["Employees"], summary="Update skill", description="Update a skill/qualification definition. Requires Admin role.")
def update_skill(
    skill_id: str, body: SkillUpdate, _cur_user: dict = Depends(require_admin)
):
    data = _load_skills()
    for s in data["skills"]:
        if s["id"] == skill_id:
            if body.name is not None:
                s["name"] = body.name  # noqa: E701
            if body.description is not None:
                s["description"] = body.description  # noqa: E701
            if body.color is not None:
                s["color"] = body.color  # noqa: E701
            if body.icon is not None:
                s["icon"] = body.icon  # noqa: E701
            if body.category is not None:
                s["category"] = body.category  # noqa: E701
            _save_skills(data)
            return s
    raise HTTPException(status_code=404, detail="Qualifikation nicht gefunden")


@router.delete("/api/skills/{skill_id}", tags=["Employees"], summary="Delete skill", description="Delete a skill/qualification definition. Requires Admin role.")
def delete_skill(skill_id: str, _cur_user: dict = Depends(require_admin)):
    data = _load_skills()
    data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
    data["assignments"] = [a for a in data["assignments"] if a["skill_id"] != skill_id]
    _save_skills(data)
    return {"ok": True}


@router.get(
    "/api/skills/assignments", tags=["Employees"], summary="List skill assignments",
    description="Return skill/qualification assignments for employees.",
)
def get_assignments(employee_id: int | None = Query(None)):
    data = _load_skills()
    assignments = data.get("assignments", [])
    if employee_id is not None:
        assignments = [a for a in assignments if a.get("employee_id") == employee_id]
    return assignments


@router.post(
    "/api/skills/assignments", tags=["Employees"], summary="Assign skill to employee",
    description="Assign a skill/qualification to an employee. Requires Admin role.",
)
def add_assignment(body: SkillAssignment, _cur_user: dict = Depends(require_admin)):
    data = _load_skills()
    # Remove existing assignment for same employee+skill
    data["assignments"] = [
        a
        for a in data.get("assignments", [])
        if not (a["employee_id"] == body.employee_id and a["skill_id"] == body.skill_id)
    ]
    assignment = {
        "id": str(_uuid.uuid4())[:8],
        "employee_id": body.employee_id,
        "skill_id": body.skill_id,
        "level": body.level or 1,
        "certified_until": body.certified_until or None,
        "notes": body.notes or "",
        "assigned_at": _dt.now().isoformat(timespec="seconds"),
    }
    data["assignments"].append(assignment)
    _save_skills(data)
    return assignment


@router.delete(
    "/api/skills/assignments/{assignment_id}",
    tags=["Employees"],
    summary="Remove skill assignment",
    description="Remove a skill/qualification assignment from an employee. Requires Admin role.",
)
def delete_assignment(assignment_id: str, _cur_user: dict = Depends(require_admin)):
    data = _load_skills()
    before = len(data.get("assignments", []))
    data["assignments"] = [
        a for a in data.get("assignments", []) if a.get("id") != assignment_id
    ]
    if len(data["assignments"]) == before:
        raise HTTPException(status_code=404, detail="Zuweisung nicht gefunden")
    _save_skills(data)
    return {"ok": True}


@router.get("/api/skills/matrix", tags=["Employees"], summary="Skills matrix overview", description="Full matrix: all employees × all skills with assignment details.")
def get_skills_matrix(_cur_user: dict = Depends(require_auth)):
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
        result_employees.append(
            {
                "id": eid,
                "name": f"{emp.get('NAME', '')} {emp.get('FIRSTNAME', '')}".strip(),
                "short": emp.get("SHORTNAME", ""),
                "group": emp.get("GROUP_NAME", ""),
                "skills": emp_skills.get(eid, {}),
                "skill_count": len(emp_skills.get(eid, {})),
            }
        )

    # Skill coverage stats
    skill_stats = []
    for skill in skills:
        sid = skill["id"]
        holders = [a for a in assignments if a["skill_id"] == sid]
        experts = [a for a in holders if a.get("level", 1) >= 3]
        expiring = []
        _dt.today().date().isoformat()
        soon = (
            _dt.today()
            .date()
            .replace(
                year=_dt.today().date().year,
                month=min(_dt.today().date().month + 3, 12),
            )
            .isoformat()
        )
        for a in holders:
            cu = a.get("certified_until")
            if cu and cu <= soon:
                expiring.append(a)
        skill_stats.append(
            {
                **skill,
                "holder_count": len(holders),
                "expert_count": len(experts),
                "expiring_count": len(expiring),
                "coverage_pct": round(len(holders) / len(employees) * 100)
                if employees
                else 0,
            }
        )

    return {
        "skills": skill_stats,
        "employees": result_employees,
        "assignments": assignments,
        "total_employees": len(employees),
    }
