"""Employees and groups router."""
import os
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from ..dependencies import (
    get_db, require_admin, _sanitize_500, _logger,
)

router = APIRouter()



@router.get("/api/employees", tags=["Employees"], summary="List employees", description="Return all active employees. Set include_hidden=true to include hidden/archived employees.")
def get_employees(include_hidden: bool = False):
    return get_db().get_employees(include_hidden=include_hidden)


@router.get("/api/employees/{emp_id}", tags=["Employees"], summary="Get employee by ID")
def get_employee(emp_id: int):
    e = get_db().get_employee(emp_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden")
    return e


@router.get("/api/groups", tags=["Groups"], summary="List groups", description="Return all groups. Set include_hidden=true to include hidden/archived groups.")
def get_groups(include_hidden: bool = False):
    db = get_db()
    groups = db.get_groups(include_hidden=include_hidden)
    # Fetch all group→members in a single pass to avoid N+1
    all_members = db.get_all_group_members()
    for g in groups:
        g['member_count'] = len(all_members.get(g['ID'], []))
    return groups


@router.get("/api/groups/{group_id}/members", tags=["Groups"], summary="List group members", description="Return all employees assigned to the given group.")
def get_group_members(group_id: int):
    db = get_db()
    member_ids = db.get_group_members(group_id)
    employees = db.get_employees(include_hidden=True)
    emp_map = {e['ID']: e for e in employees}
    return [emp_map[mid] for mid in member_ids if mid in emp_map]


# ── Write: Employees ─────────────────────────────────────────

_DATE_PATTERN = r'^\d{4}-\d{2}-\d{2}$'


def _validate_date_field(v: str, field_name: str) -> str:
    """Validate optional date string is YYYY-MM-DD if non-empty."""
    if v:
        import re as _re
        from datetime import datetime as _dtt
        if not _re.match(_DATE_PATTERN, v):
            raise ValueError(f"'{field_name}' muss im Format YYYY-MM-DD sein")
        try:
            _dtt.strptime(v, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f"'{field_name}' ist kein gültiges Datum")
    return v


class EmployeeCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100, description="Nachname (Pflichtfeld)")
    FIRSTNAME: str = Field('', max_length=100)
    SHORTNAME: str = Field('', max_length=20)
    NUMBER: str = Field('', max_length=20)
    SEX: int = Field(0, ge=0, le=2)
    HRSDAY: float = Field(0.0, ge=0.0, le=24.0)
    HRSWEEK: float = Field(0.0, ge=0.0, le=168.0)
    HRSMONTH: float = Field(0.0, ge=0.0, le=744.0)
    HRSTOTAL: float = Field(0.0, ge=0.0)
    WORKDAYS: str = Field('1 1 1 1 1 0 0 0', max_length=20)
    HIDE: bool = False
    BOLD: int = Field(0, ge=0, le=1)
    # Personal data
    SALUTATION: str = Field('', max_length=50)
    STREET: str = Field('', max_length=200)
    ZIP: str = Field('', max_length=20)
    TOWN: str = Field('', max_length=100)
    PHONE: str = Field('', max_length=50)
    EMAIL: str = Field('', max_length=200)
    FUNCTION: str = Field('', max_length=100)
    BIRTHDAY: str = Field('', max_length=10)
    EMPSTART: str = Field('', max_length=10)
    EMPEND: str = Field('', max_length=10)
    # Calculation settings
    CALCBASE: int = Field(0, ge=0)
    DEDUCTHOL: int = Field(0, ge=0, le=1)
    # Free text fields
    NOTE1: str = Field('', max_length=500)
    NOTE2: str = Field('', max_length=500)
    NOTE3: str = Field('', max_length=500)
    NOTE4: str = Field('', max_length=500)
    ARBITR1: str = Field('', max_length=200)
    ARBITR2: str = Field('', max_length=200)
    ARBITR3: str = Field('', max_length=200)
    # Colors (BGR int: 0=black, 16777215=white)
    CFGLABEL: Optional[int] = Field(None, ge=0, le=16777215)
    CBKLABEL: Optional[int] = Field(None, ge=0, le=16777215)
    CBKSCHED: Optional[int] = Field(None, ge=0, le=16777215)

    @field_validator('NAME')
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("NAME darf nicht leer sein")
        return v

    @field_validator('BIRTHDAY', 'EMPSTART', 'EMPEND', mode='before')
    @classmethod
    def validate_dates(cls, v):
        if v is None:
            return ''
        return _validate_date_field(str(v), 'Datum')

    @model_validator(mode='after')
    def empend_after_empstart(self) -> 'EmployeeCreate':
        if self.EMPSTART and self.EMPEND and self.EMPEND < self.EMPSTART:
            raise ValueError("EMPEND muss >= EMPSTART sein")
        return self


class EmployeeUpdate(BaseModel):
    NAME: Optional[str] = Field(None, min_length=1, max_length=100)
    FIRSTNAME: Optional[str] = Field(None, max_length=100)
    SHORTNAME: Optional[str] = Field(None, max_length=20)
    NUMBER: Optional[str] = Field(None, max_length=20)
    SEX: Optional[int] = Field(None, ge=0, le=2)
    HRSDAY: Optional[float] = Field(None, ge=0.0, le=24.0)
    HRSWEEK: Optional[float] = Field(None, ge=0.0, le=168.0)
    HRSMONTH: Optional[float] = Field(None, ge=0.0, le=744.0)
    HRSTOTAL: Optional[float] = Field(None, ge=0.0)
    WORKDAYS: Optional[str] = Field(None, max_length=20)
    HIDE: Optional[bool] = None
    BOLD: Optional[int] = Field(None, ge=0, le=1)
    POSITION: Optional[int] = None
    # Personal data
    SALUTATION: Optional[str] = Field(None, max_length=50)
    STREET: Optional[str] = Field(None, max_length=200)
    ZIP: Optional[str] = Field(None, max_length=20)
    TOWN: Optional[str] = Field(None, max_length=100)
    PHONE: Optional[str] = Field(None, max_length=50)
    EMAIL: Optional[str] = Field(None, max_length=200)
    FUNCTION: Optional[str] = Field(None, max_length=100)
    BIRTHDAY: Optional[str] = Field(None, max_length=10)
    EMPSTART: Optional[str] = Field(None, max_length=10)
    EMPEND: Optional[str] = Field(None, max_length=10)
    # Calculation settings
    CALCBASE: Optional[int] = Field(None, ge=0)
    DEDUCTHOL: Optional[int] = Field(None, ge=0, le=1)
    # Free text fields
    NOTE1: Optional[str] = Field(None, max_length=500)
    NOTE2: Optional[str] = Field(None, max_length=500)
    NOTE3: Optional[str] = Field(None, max_length=500)
    NOTE4: Optional[str] = Field(None, max_length=500)
    ARBITR1: Optional[str] = Field(None, max_length=200)
    ARBITR2: Optional[str] = Field(None, max_length=200)
    ARBITR3: Optional[str] = Field(None, max_length=200)
    # Colors (BGR int)
    CFGLABEL: Optional[int] = Field(None, ge=0, le=16777215)
    CBKLABEL: Optional[int] = Field(None, ge=0, le=16777215)
    CBKSCHED: Optional[int] = Field(None, ge=0, le=16777215)

    @field_validator('NAME')
    @classmethod
    def name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("NAME darf nicht leer sein")
        return v

    @field_validator('BIRTHDAY', 'EMPSTART', 'EMPEND', mode='before')
    @classmethod
    def validate_dates(cls, v):
        if v is None or v == '':
            return v
        return _validate_date_field(str(v), 'Datum')


@router.post("/api/employees", tags=["Employees"], summary="Create employee", description="Create a new employee record. Requires Admin role.")
def create_employee(body: EmployeeCreate, _cur_user: dict = Depends(require_admin)):
    # Validation handled by Pydantic Field constraints and validators
    try:
        result = get_db().create_employee(body.model_dump())
        _logger.warning(
            "AUDIT EMPLOYEE_CREATE | user=%s name=%s shortname=%s id=%s",
            _cur_user.get('NAME'), body.NAME, body.SHORTNAME, result.get('ID')
        )
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith('DUPLICATE:SHORTNAME:'):
            sn = (body.SHORTNAME or '').strip()
            raise HTTPException(status_code=409, detail=f"Kürzel '{sn}' ist bereits vergeben")
        raise _sanitize_500(e, 'create_employee')
    except Exception as e:
        raise _sanitize_500(e, 'create_employee')


@router.put("/api/employees/{emp_id}", tags=["Employees"], summary="Update employee", description="Update an existing employee. Requires Admin role.")
def update_employee(emp_id: int, body: EmployeeUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        # Validation handled by Pydantic Field constraints and validators
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_employee(emp_id, data)
        _logger.warning(
            "AUDIT EMPLOYEE_UPDATE | user=%s emp_id=%d fields=%s",
            _cur_user.get('NAME'), emp_id, list(data.keys())
        )
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_employee/{emp_id}')


@router.delete("/api/employees/{emp_id}", tags=["Employees"], summary="Delete (hide) employee", description="Marks an employee as hidden. Requires Admin role.")
def delete_employee(emp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_employee(emp_id)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden")
        _logger.warning(
            "AUDIT EMPLOYEE_DELETE | user=%s emp_id=%d",
            _cur_user.get('NAME'), emp_id
        )
        return {"ok": True, "hidden": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f'delete_employee/{emp_id}')


# ── Employee Photo Upload ─────────────────────────────────────

_PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'uploads', 'photos')


@router.get("/api/employees/{emp_id}/photo")
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


@router.post("/api/groups")
def create_group(body: GroupCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    try:
        result = get_db().create_group(body.model_dump())
        _logger.warning("AUDIT GROUP_CREATE | user=%s name=%s id=%s", _cur_user.get('NAME'), body.NAME, result.get('ID'))
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, 'create_group')


@router.put("/api/groups/{group_id}")
def update_group(group_id: int, body: GroupUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_group(group_id, data)
        _logger.warning("AUDIT GROUP_UPDATE | user=%s group_id=%d fields=%s", _cur_user.get('NAME'), group_id, list(data.keys()))
        return {"ok": True, "record": result}
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Gruppe ID {group_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_group/{group_id}')


@router.delete("/api/groups/{group_id}")
def delete_group(group_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_group(group_id)
        _logger.warning("AUDIT GROUP_DELETE | user=%s group_id=%d", _cur_user.get('NAME'), group_id)
        return {"ok": True, "hidden": count}
    except Exception as e:
        raise _sanitize_500(e, f'delete_group/{group_id}')


@router.post("/api/groups/{group_id}/members")
def add_group_member(group_id: int, body: GroupMemberBody, _cur_user: dict = Depends(require_admin)):
    try:
        result = get_db().add_group_member(group_id, body.employee_id)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, f'add_group_member/{group_id}')


@router.delete("/api/groups/{group_id}/members/{emp_id}")
def remove_group_member(group_id: int, emp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().remove_group_member(group_id, emp_id)
        return {"ok": True, "removed": count}
    except Exception as e:
        raise _sanitize_500(e, f'remove_group_member/{group_id}/{emp_id}')



# ── Import endpoints ─────────────────────────────────────────



@router.post("/api/employees/{emp_id}/photo")
async def upload_employee_photo(emp_id: int, file: UploadFile = File(...), _cur_user: dict = Depends(require_admin)):
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
    _MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB
    if len(content) > _MAX_PHOTO_SIZE:
        raise HTTPException(status_code=413, detail="Datei zu groß (max. 5 MB)")
    dest.write_bytes(content)

    rel_path = f"uploads/photos/{emp_id}{ext}"
    try:
        db.update_employee(emp_id, {'PHOTO': rel_path})
    except Exception:
        pass  # best effort

    return {"ok": True, "photo_url": f"/api/employees/{emp_id}/photo", "path": rel_path}


# ── Bulk Operations ─────────────────────────────────────────

class BulkEmployeeAction(BaseModel):
    employee_ids: List[int] = Field(..., min_length=1, description="Liste von Mitarbeiter-IDs")
    action: str = Field(..., description="'hide', 'show', 'assign_group', 'remove_group'")
    group_id: Optional[int] = Field(None, description="Ziel-Gruppe für assign_group/remove_group")


@router.post("/api/employees/bulk", tags=["Employees"], summary="Bulk employee actions")
def bulk_employee_action(body: BulkEmployeeAction, _cur_user: dict = Depends(require_admin)):
    """Bulk operations: hide/show employees or assign/remove them from a group."""
    db = get_db()
    results = {"ok": True, "affected": 0, "errors": []}

    if body.action in ('hide', 'show'):
        hide_val = body.action == 'hide'
        for emp_id in body.employee_ids:
            try:
                db.update_employee(emp_id, {'HIDE': hide_val})
                results["affected"] += 1
            except Exception as e:
                results["errors"].append({"id": emp_id, "error": str(e)})

    elif body.action == 'assign_group':
        if not body.group_id:
            raise HTTPException(status_code=400, detail="group_id erforderlich für assign_group")
        for emp_id in body.employee_ids:
            try:
                # add_group_member is idempotent (ignore duplicate)
                db.add_group_member(body.group_id, emp_id)
                results["affected"] += 1
            except Exception as e:
                results["errors"].append({"id": emp_id, "error": str(e)})

    elif body.action == 'remove_group':
        if not body.group_id:
            raise HTTPException(status_code=400, detail="group_id erforderlich für remove_group")
        for emp_id in body.employee_ids:
            try:
                db.remove_group_member(body.group_id, emp_id)
                results["affected"] += 1
            except Exception as e:
                results["errors"].append({"id": emp_id, "error": str(e)})

    else:
        raise HTTPException(status_code=400, detail=f"Unbekannte Aktion: {body.action}")

    return results
