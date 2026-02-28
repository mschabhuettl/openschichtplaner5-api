"""Master data router: shifts, leave-types, workplaces, holidays, extracharges, staffing-requirements, skills."""
from fastapi import APIRouter, HTTPException, Query, Header, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from ..dependencies import (
    get_db, require_admin, require_planer, require_auth, require_role,
    _sanitize_500, _logger, get_current_user,
)

router = APIRouter()



@router.get("/api/shifts")
def get_shifts(include_hidden: bool = False):
    return get_db().get_shifts(include_hidden=include_hidden)


@router.get("/api/leave-types")
def get_leave_types(include_hidden: bool = False):
    return get_db().get_leave_types(include_hidden=include_hidden)


@router.get("/api/workplaces")
def get_workplaces(include_hidden: bool = False):
    return get_db().get_workplaces(include_hidden=include_hidden)


@router.get("/api/holidays")
def get_holidays(year: Optional[int] = None):
    return get_db().get_holidays(year=year)


# ── Staffing Requirements ─────────────────────────────────────

@router.get("/api/staffing-requirements")
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


# ── Staffing Requirements Write ──────────────────────────────

class StaffingRequirementSet(BaseModel):
    shift_id: int
    weekday: int
    min: int
    max: int
    group_id: int


@router.post("/api/staffing-requirements")
def set_staffing_requirement(body: StaffingRequirementSet, _cur_user: dict = Depends(require_planer)):
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
        raise _sanitize_500(e)


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


@router.post("/api/shifts")
def create_shift(body: ShiftCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if body.DURATION0 is not None and body.DURATION0 < 0:
        raise HTTPException(status_code=400, detail="Feld 'DURATION0' darf nicht negativ sein")
    try:
        result = get_db().create_shift(body.model_dump())
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith('DUPLICATE:SHIFTNAME:'):
            raise HTTPException(status_code=409, detail=f"Schicht mit dem Namen '{body.NAME}' existiert bereits")
        raise _sanitize_500(e, 'create_shift')
    except Exception as e:
        raise _sanitize_500(e, 'create_shift')


@router.put("/api/shifts/{shift_id}")
def update_shift(shift_id: int, body: ShiftUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_shift(shift_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Schicht ID {shift_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_shift/{shift_id}')


@router.delete("/api/shifts/{shift_id}")
def hide_shift(shift_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().hide_shift(shift_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e, f'hide_shift/{shift_id}')


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


@router.post("/api/leave-types")
def create_leave_type(body: LeaveTypeCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if body.STDENTIT is not None and body.STDENTIT < 0:
        raise HTTPException(status_code=400, detail="Feld 'STDENTIT' darf nicht negativ sein")
    try:
        result = get_db().create_leave_type(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, 'create_leave_type')


@router.put("/api/leave-types/{lt_id}")
def update_leave_type(lt_id: int, body: LeaveTypeUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_leave_type(lt_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Abwesenheitstyp ID {lt_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_leave_type/{lt_id}')


@router.delete("/api/leave-types/{lt_id}")
def hide_leave_type(lt_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().hide_leave_type(lt_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e, f'hide_leave_type/{lt_id}')


# ── Write: Holidays ───────────────────────────────────────────

class HolidayCreate(BaseModel):
    DATE: str
    NAME: str
    INTERVAL: int = 0


class HolidayUpdate(BaseModel):
    DATE: Optional[str] = None
    NAME: Optional[str] = None
    INTERVAL: Optional[int] = None


@router.post("/api/holidays")
def create_holiday(body: HolidayCreate, _cur_user: dict = Depends(require_admin)):
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
        raise _sanitize_500(e)


@router.put("/api/holidays/{holiday_id}")
def update_holiday(holiday_id: int, body: HolidayUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_holiday(holiday_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/holidays/{holiday_id}")
def delete_holiday(holiday_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_holiday(holiday_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


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


@router.post("/api/workplaces")
def create_workplace(body: WorkplaceCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="NAME darf nicht leer sein")
    try:
        result = get_db().create_workplace(body.model_dump())
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.put("/api/workplaces/{wp_id}")
def update_workplace(wp_id: int, body: WorkplaceUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_workplace(wp_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/workplaces/{wp_id}")
def hide_workplace(wp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().hide_workplace(wp_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Workplace ↔ Employee Assignments ──────────────────────────

@router.get("/api/workplaces/{wp_id}/employees")
def get_workplace_employees(wp_id: int):
    """Return employees assigned to a workplace."""
    try:
        return get_db().get_workplace_employees(wp_id)
    except Exception as e:
        raise _sanitize_500(e)


@router.post("/api/workplaces/{wp_id}/employees/{employee_id}")
def assign_employee_to_workplace(wp_id: int, employee_id: int, _cur_user: dict = Depends(require_admin)):
    """Assign an employee to a workplace."""
    try:
        added = get_db().assign_employee_to_workplace(employee_id, wp_id)
        return {"ok": True, "added": added}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/workplaces/{wp_id}/employees/{employee_id}")
def remove_employee_from_workplace(wp_id: int, employee_id: int, _cur_user: dict = Depends(require_admin)):
    """Remove an employee from a workplace."""
    try:
        removed = get_db().remove_employee_from_workplace(employee_id, wp_id)
        return {"ok": True, "removed": removed}
    except Exception as e:
        raise _sanitize_500(e)


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


@router.get("/api/extracharges")
def get_extracharges(include_hidden: bool = False):
    return get_db().get_extracharges(include_hidden=include_hidden)


@router.post("/api/extracharges")
def create_extracharge(body: ExtraChargeCreate, _cur_user: dict = Depends(require_admin)):
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
        raise _sanitize_500(e)


@router.get("/api/extracharges/summary")
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
        raise _sanitize_500(e)


@router.put("/api/extracharges/{xc_id}")
def update_extracharge(xc_id: int, body: ExtraChargeUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_extracharge(xc_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/extracharges/{xc_id}")
def delete_extracharge(xc_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_extracharge(xc_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Special Staffing Requirements (SPDEM) ────────────────────

@router.get("/api/staffing-requirements/special")
def get_special_staffing(
    date: Optional[str] = Query(None, description="Date filter YYYY-MM-DD"),
    group_id: Optional[int] = Query(None, description="Group ID filter"),
):
    """Return date-specific staffing requirements from 5SPDEM.DBF."""
    try:
        return get_db().get_special_staffing(date=date, group_id=group_id)
    except Exception as e:
        raise _sanitize_500(e)


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


@router.post("/api/staffing-requirements/special")
def create_special_staffing(body: SpecialStaffingCreate, _cur_user: dict = Depends(require_planer)):
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
        raise _sanitize_500(e)


@router.put("/api/staffing-requirements/special/{record_id}")
def update_special_staffing(record_id: int, body: SpecialStaffingUpdate, _cur_user: dict = Depends(require_planer)):
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
        raise _sanitize_500(e)


@router.delete("/api/staffing-requirements/special/{record_id}")
def delete_special_staffing(record_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a date-specific staffing requirement."""
    try:
        count = get_db().delete_special_staffing(record_id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Record not found")
        return {"ok": True, "deleted": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


# ── Kompetenz-Matrix / Skills ────────────────────────────────────
import uuid as _uuid
import os
import json as _json
from datetime import datetime as _dt

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

@router.get("/api/skills")
def get_skills():
    data = _load_skills()
    return data["skills"]

@router.post("/api/skills")
def create_skill(body: SkillCreate, _cur_user: dict = Depends(require_admin)):
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

@router.put("/api/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate, _cur_user: dict = Depends(require_admin)):
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

@router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str, _cur_user: dict = Depends(require_admin)):
    data = _load_skills()
    data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
    data["assignments"] = [a for a in data["assignments"] if a["skill_id"] != skill_id]
    _save_skills(data)
    return {"ok": True}

@router.get("/api/skills/assignments")
def get_assignments(employee_id: Optional[int] = Query(None)):
    data = _load_skills()
    assignments = data.get("assignments", [])
    if employee_id is not None:
        assignments = [a for a in assignments if a.get("employee_id") == employee_id]
    return assignments

@router.post("/api/skills/assignments")
def add_assignment(body: SkillAssignment, _cur_user: dict = Depends(require_admin)):
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

@router.delete("/api/skills/assignments/{assignment_id}")
def delete_assignment(assignment_id: str, _cur_user: dict = Depends(require_admin)):
    data = _load_skills()
    before = len(data.get("assignments", []))
    data["assignments"] = [a for a in data.get("assignments", []) if a.get("id") != assignment_id]
    if len(data["assignments"]) == before:
        raise HTTPException(status_code=404, detail="Assignment not found")
    _save_skills(data)
    return {"ok": True}

@router.get("/api/skills/matrix")
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
