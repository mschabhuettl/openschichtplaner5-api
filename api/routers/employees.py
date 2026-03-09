"""Employees and groups router."""

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from ..dependencies import (
    _logger,
    _sanitize_500,
    get_db,
    require_admin,
)
from ..schemas import EmployeeResponse, GroupResponse

router = APIRouter()


@router.get(
    "/api/employees",
    tags=["Employees"],
    summary="List employees",
    description="Return all active employees. Set include_hidden=true to include hidden/archived employees.",
    response_model=list[EmployeeResponse],
)
def get_employees(include_hidden: bool = False):
    return get_db().get_employees(include_hidden=include_hidden)


@router.get("/api/employees/{emp_id}", tags=["Employees"], summary="Get employee by ID")
def get_employee(emp_id: int):
    e = get_db().get_employee(emp_id)
    if e is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
        )
    return e


@router.get(
    "/api/groups",
    tags=["Groups"],
    summary="List groups",
    description="Return all groups. Set include_hidden=true to include hidden/archived groups.",
    response_model=list[GroupResponse],
)
def get_groups(include_hidden: bool = False):
    db = get_db()
    groups = db.get_groups(include_hidden=include_hidden)
    # Fetch all group→members in a single pass to avoid N+1
    all_members = db.get_all_group_members()
    for g in groups:
        g["member_count"] = len(all_members.get(g["ID"], []))
    return groups


@router.get(
    "/api/groups/{group_id}/members",
    tags=["Groups"],
    summary="List group members",
    description="Return all employees assigned to the given group.",
)
def get_group_members(group_id: int):
    db = get_db()
    member_ids = db.get_group_members(group_id)
    employees = db.get_employees(include_hidden=True)
    emp_map = {e["ID"]: e for e in employees}
    return [emp_map[mid] for mid in member_ids if mid in emp_map]


# ── Write: Employees ─────────────────────────────────────────

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _validate_date_field(v: str, field_name: str) -> str:
    """Validate optional date string is YYYY-MM-DD if non-empty."""
    if v:
        import re as _re
        from datetime import datetime as _dtt

        if not _re.match(_DATE_PATTERN, v):
            raise ValueError(f"'{field_name}' muss im Format YYYY-MM-DD sein")
        try:
            _dtt.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"'{field_name}' ist kein gültiges Datum")
    return v


class EmployeeCreate(BaseModel):
    NAME: str = Field(
        ..., min_length=1, max_length=100, description="Nachname (Pflichtfeld)"
    )
    FIRSTNAME: str = Field("", max_length=100)
    SHORTNAME: str = Field("", max_length=20)
    NUMBER: str = Field("", max_length=20)
    SEX: int = Field(0, ge=0, le=2)
    HRSDAY: float = Field(0.0, ge=0.0, le=24.0)
    HRSWEEK: float = Field(0.0, ge=0.0, le=168.0)
    HRSMONTH: float = Field(0.0, ge=0.0, le=744.0)
    HRSTOTAL: float = Field(0.0, ge=0.0)
    WORKDAYS: str = Field("1 1 1 1 1 0 0 0", max_length=20)
    HIDE: bool = False
    BOLD: int = Field(0, ge=0, le=1)
    # Personal data
    SALUTATION: str = Field("", max_length=50)
    STREET: str = Field("", max_length=200)
    ZIP: str = Field("", max_length=20)
    TOWN: str = Field("", max_length=100)
    PHONE: str = Field("", max_length=50)
    EMAIL: str = Field("", max_length=200)
    FUNCTION: str = Field("", max_length=100)
    BIRTHDAY: str = Field("", max_length=10)
    EMPSTART: str = Field("", max_length=10)
    EMPEND: str = Field("", max_length=10)
    # Calculation settings
    CALCBASE: int = Field(0, ge=0)
    DEDUCTHOL: int = Field(0, ge=0, le=1)
    # Free text fields
    NOTE1: str = Field("", max_length=500)
    NOTE2: str = Field("", max_length=500)
    NOTE3: str = Field("", max_length=500)
    NOTE4: str = Field("", max_length=500)
    ARBITR1: str = Field("", max_length=200)
    ARBITR2: str = Field("", max_length=200)
    ARBITR3: str = Field("", max_length=200)
    # Colors (BGR int: 0=black, 16777215=white)
    CFGLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKSCHED: int | None = Field(None, ge=0, le=16777215)

    @field_validator("NAME")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("NAME darf nicht leer sein")
        return v

    @field_validator("BIRTHDAY", "EMPSTART", "EMPEND", mode="before")
    @classmethod
    def validate_dates(cls, v):
        if v is None:
            return ""
        return _validate_date_field(str(v), "Datum")

    @model_validator(mode="after")
    def empend_after_empstart(self) -> "EmployeeCreate":
        if self.EMPSTART and self.EMPEND and self.EMPEND < self.EMPSTART:
            raise ValueError("EMPEND muss >= EMPSTART sein")
        return self


class EmployeeUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    FIRSTNAME: str | None = Field(None, max_length=100)
    SHORTNAME: str | None = Field(None, max_length=20)
    NUMBER: str | None = Field(None, max_length=20)
    SEX: int | None = Field(None, ge=0, le=2)
    HRSDAY: float | None = Field(None, ge=0.0, le=24.0)
    HRSWEEK: float | None = Field(None, ge=0.0, le=168.0)
    HRSMONTH: float | None = Field(None, ge=0.0, le=744.0)
    HRSTOTAL: float | None = Field(None, ge=0.0)
    WORKDAYS: str | None = Field(None, max_length=20)
    HIDE: bool | None = None
    BOLD: int | None = Field(None, ge=0, le=1)
    POSITION: int | None = None
    # Personal data
    SALUTATION: str | None = Field(None, max_length=50)
    STREET: str | None = Field(None, max_length=200)
    ZIP: str | None = Field(None, max_length=20)
    TOWN: str | None = Field(None, max_length=100)
    PHONE: str | None = Field(None, max_length=50)
    EMAIL: str | None = Field(None, max_length=200)
    FUNCTION: str | None = Field(None, max_length=100)
    BIRTHDAY: str | None = Field(None, max_length=10)
    EMPSTART: str | None = Field(None, max_length=10)
    EMPEND: str | None = Field(None, max_length=10)
    # Calculation settings
    CALCBASE: int | None = Field(None, ge=0)
    DEDUCTHOL: int | None = Field(None, ge=0, le=1)
    # Free text fields
    NOTE1: str | None = Field(None, max_length=500)
    NOTE2: str | None = Field(None, max_length=500)
    NOTE3: str | None = Field(None, max_length=500)
    NOTE4: str | None = Field(None, max_length=500)
    ARBITR1: str | None = Field(None, max_length=200)
    ARBITR2: str | None = Field(None, max_length=200)
    ARBITR3: str | None = Field(None, max_length=200)
    # Colors (BGR int)
    CFGLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKSCHED: int | None = Field(None, ge=0, le=16777215)

    @field_validator("NAME")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("NAME darf nicht leer sein")
        return v

    @field_validator("BIRTHDAY", "EMPSTART", "EMPEND", mode="before")
    @classmethod
    def validate_dates(cls, v):
        if v is None or v == "":
            return v
        return _validate_date_field(str(v), "Datum")


@router.post(
    "/api/employees",
    tags=["Employees"],
    summary="Create employee",
    description="Create a new employee record. Requires Admin role.",
)
def create_employee(body: EmployeeCreate, _cur_user: dict = Depends(require_admin)):
    # Validation handled by Pydantic Field constraints and validators
    try:
        db = get_db()
        result = db.create_employee(body.model_dump())
        _logger.warning(
            "AUDIT EMPLOYEE_CREATE | user=%s name=%s shortname=%s id=%s",
            _cur_user.get("NAME"),
            body.NAME,
            body.SHORTNAME,
            result.get("ID"),
        )
        # Audit: employee created
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="CREATE",
            entity="employee",
            entity_id=result.get("ID", 0),
            details=f"Mitarbeiter erstellt: {body.NAME} ({body.SHORTNAME})",
            new_value={"NAME": body.NAME, "SHORTNAME": body.SHORTNAME},
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith("DUPLICATE:SHORTNAME:"):
            sn = (body.SHORTNAME or "").strip()
            raise HTTPException(
                status_code=409, detail=f"Kürzel '{sn}' ist bereits vergeben"
            )
        raise _sanitize_500(e, "create_employee")
    except Exception as e:
        raise _sanitize_500(e, "create_employee")


@router.put(
    "/api/employees/{emp_id}",
    tags=["Employees"],
    summary="Update employee",
    description="Update an existing employee. Requires Admin role.",
)
def update_employee(
    emp_id: int, body: EmployeeUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        # Validation handled by Pydantic Field constraints and validators
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        db = get_db()
        # Capture old state for audit
        old_emp = db.get_employee(emp_id)
        old_snapshot = None
        if old_emp:
            old_snapshot = {k: old_emp.get(k) for k in data.keys() if k in (old_emp or {})}
        result = db.update_employee(emp_id, data)
        _logger.warning(
            "AUDIT EMPLOYEE_UPDATE | user=%s emp_id=%d fields=%s",
            _cur_user.get("NAME"),
            emp_id,
            list(data.keys()),
        )
        # Audit: employee updated with old/new values
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="UPDATE",
            entity="employee",
            entity_id=emp_id,
            details=f"Mitarbeiter {emp_id} aktualisiert: {', '.join(data.keys())}",
            old_value=old_snapshot,
            new_value=data,
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
        )
    except Exception as e:
        raise _sanitize_500(e, f"update_employee/{emp_id}")


@router.delete(
    "/api/employees/{emp_id}",
    tags=["Employees"],
    summary="Delete (hide) employee",
    description="Marks an employee as hidden. Requires Admin role.",
)
def delete_employee(emp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        db = get_db()
        # Capture old name for audit
        old_emp = db.get_employee(emp_id)
        old_name = old_emp.get("NAME", "?") if old_emp else "?"
        count = db.delete_employee(emp_id)
        if count == 0:
            raise HTTPException(
                status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
            )
        _logger.warning(
            "AUDIT EMPLOYEE_DELETE | user=%s emp_id=%d", _cur_user.get("NAME"), emp_id
        )
        # Audit: employee hidden/deleted
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="DELETE",
            entity="employee",
            entity_id=emp_id,
            details=f"Mitarbeiter {old_name} (ID {emp_id}) ausgeblendet",
            old_value={"NAME": old_name},
            user_id=_cur_user.get("ID"),
        )
        return {"ok": True, "hidden": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f"delete_employee/{emp_id}")


# ── Employee Photo Upload ─────────────────────────────────────

_PHOTOS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "uploads", "photos"
)


@router.get(
    "/api/employees/{emp_id}/photo", tags=["Employees"], summary="Get employee photo"
)
async def get_employee_photo(emp_id: int):
    import pathlib

    from fastapi.responses import FileResponse as _FileResponse

    photos_dir = pathlib.Path(_PHOTOS_DIR)
    for ext in (".jpg", ".jpeg", ".png", ".gif"):
        p = photos_dir / f"{emp_id}{ext}"
        if p.exists():
            return _FileResponse(str(p))
    raise HTTPException(status_code=404, detail="Kein Foto vorhanden")


# ── Write: Groups ─────────────────────────────────────────────


class GroupCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    SHORTNAME: str = Field("", max_length=20)
    SUPERID: int = 0
    HIDE: bool = False
    BOLD: int = 0
    DAILYDEM: int = 0
    ARBITR: str = ""
    CFGLABEL: int | None = None
    CBKLABEL: int | None = None
    CBKSCHED: int | None = None


class GroupUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    SHORTNAME: str | None = Field(None, max_length=20)
    SUPERID: int | None = None
    POSITION: int | None = None
    HIDE: bool | None = None
    BOLD: int | None = None
    DAILYDEM: int | None = None
    ARBITR: str | None = None
    CFGLABEL: int | None = None
    CBKLABEL: int | None = None
    CBKSCHED: int | None = None

    @field_validator("NAME")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Gruppenname darf nicht leer sein")
        return v.strip() if v is not None else v


class GroupMemberBody(BaseModel):
    employee_id: int


@router.post(
    "/api/groups",
    tags=["Groups"],
    summary="Create group",
    description="Create a new employee group. Requires Admin role.",
)
def create_group(body: GroupCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    try:
        result = get_db().create_group(body.model_dump())
        _logger.warning(
            "AUDIT GROUP_CREATE | user=%s name=%s id=%s",
            _cur_user.get("NAME"),
            body.NAME,
            result.get("ID"),
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, "create_group")


@router.put(
    "/api/groups/{group_id}",
    tags=["Groups"],
    summary="Update group",
    description="Update name, shortname, or color of an existing group. Requires Admin role.",
)
def update_group(
    group_id: int, body: GroupUpdate, _cur_user: dict = Depends(require_admin)
):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_group(group_id, data)
        _logger.warning(
            "AUDIT GROUP_UPDATE | user=%s group_id=%d fields=%s",
            _cur_user.get("NAME"),
            group_id,
            list(data.keys()),
        )
        return {"ok": True, "record": result}
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Gruppe ID {group_id} nicht gefunden"
        )
    except Exception as e:
        raise _sanitize_500(e, f"update_group/{group_id}")


@router.delete(
    "/api/groups/{group_id}",
    tags=["Groups"],
    summary="Delete group",
    description="Soft-delete (hide) a group. Members are not removed. Requires Admin role.",
)
def delete_group(group_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_group(group_id)
        _logger.warning(
            "AUDIT GROUP_DELETE | user=%s group_id=%d", _cur_user.get("NAME"), group_id
        )
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e, f"delete_group/{group_id}")


@router.post(
    "/api/groups/{group_id}/members",
    tags=["Groups"],
    summary="Add group member",
    description='Add an employee to a group. Body: `{"employee_id": <int>}`. Requires Admin role.',
)
def add_group_member(
    group_id: int, body: GroupMemberBody, _cur_user: dict = Depends(require_admin)
):
    try:
        result = get_db().add_group_member(group_id, body.employee_id)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, f"add_group_member/{group_id}")


@router.delete(
    "/api/groups/{group_id}/members/{emp_id}",
    tags=["Groups"],
    summary="Remove group member",
    description="Remove a specific employee from a group. Requires Admin role.",
)
def remove_group_member(
    group_id: int, emp_id: int, _cur_user: dict = Depends(require_admin)
):
    try:
        count = get_db().remove_group_member(group_id, emp_id)
        return {"ok": True, "removed": count}
    except Exception as e:
        raise _sanitize_500(e, f"remove_group_member/{group_id}/{emp_id}")


# ── Import endpoints ─────────────────────────────────────────


@router.post(
    "/api/employees/{emp_id}/photo", tags=["Employees"], summary="Upload employee photo"
)
async def upload_employee_photo(
    emp_id: int, file: UploadFile = File(...), _cur_user: dict = Depends(require_admin)
):
    """Upload a photo for an employee (JPG/PNG/GIF)."""
    import pathlib

    photos_dir = pathlib.Path(_PHOTOS_DIR)
    photos_dir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {emp_id} nicht gefunden"
        )

    ct = (file.content_type or "").lower()
    allowed = ("image/jpeg", "image/png", "image/gif")
    if ct not in allowed:
        raise HTTPException(status_code=400, detail="Nur JPG, PNG oder GIF erlaubt")

    ext = ".jpg"
    if ct == "image/png":
        ext = ".png"
    elif ct == "image/gif":
        ext = ".gif"

    # Remove old photos for this employee
    for old in photos_dir.glob(f"{emp_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass

    dest = photos_dir / f"{emp_id}{ext}"
    content = await file.read()
    _MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB
    if len(content) > _MAX_PHOTO_SIZE:
        raise HTTPException(status_code=413, detail="Datei zu groß (max. 5 MB)")
    dest.write_bytes(content)

    rel_path = f"uploads/photos/{emp_id}{ext}"
    try:
        db.update_employee(emp_id, {"PHOTO": rel_path})
    except Exception:
        pass  # best effort

    return {"ok": True, "photo_url": f"/api/employees/{emp_id}/photo", "path": rel_path}


# ── Bulk Operations ─────────────────────────────────────────


class BulkEmployeeAction(BaseModel):
    employee_ids: list[int] = Field(
        ..., min_length=1, description="Liste von Mitarbeiter-IDs"
    )
    action: str = Field(
        ..., description="'hide', 'show', 'assign_group', 'remove_group'"
    )
    group_id: int | None = Field(
        None, description="Ziel-Gruppe für assign_group/remove_group"
    )


@router.post("/api/employees/bulk", tags=["Employees"], summary="Bulk employee actions")
def bulk_employee_action(
    body: BulkEmployeeAction, _cur_user: dict = Depends(require_admin)
):
    """Bulk operations: hide/show employees or assign/remove them from a group."""
    db = get_db()
    affected: int = 0
    errors: list = []

    if body.action in ("hide", "show"):
        hide_val = body.action == "hide"
        for emp_id in body.employee_ids:
            try:
                db.update_employee(emp_id, {"HIDE": hide_val})
                affected += 1
            except Exception as e:
                errors.append({"id": emp_id, "error": str(e)})

    elif body.action == "assign_group":
        if not body.group_id:
            raise HTTPException(
                status_code=400, detail="group_id erforderlich für assign_group"
            )
        for emp_id in body.employee_ids:
            try:
                # add_group_member is idempotent (ignore duplicate)
                db.add_group_member(body.group_id, emp_id)
                affected += 1
            except Exception as e:
                errors.append({"id": emp_id, "error": str(e)})

    elif body.action == "remove_group":
        if not body.group_id:
            raise HTTPException(
                status_code=400, detail="group_id erforderlich für remove_group"
            )
        for emp_id in body.employee_ids:
            try:
                db.remove_group_member(body.group_id, emp_id)
                affected += 1
            except Exception as e:
                errors.append({"id": emp_id, "error": str(e)})

    else:
        raise HTTPException(status_code=400, detail=f"Unbekannte Aktion: {body.action}")

    return {"ok": True, "affected": affected, "errors": errors}
