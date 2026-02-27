"""Employees and groups router."""
import os
import re
from fastapi import APIRouter, HTTPException, Query, Header, Depends, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from ..dependencies import (
    get_db, require_admin, require_planer, require_auth, require_role,
    _sanitize_500, _logger, get_current_user,
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


@router.get("/api/groups")
def get_groups(include_hidden: bool = False):
    db = get_db()
    groups = db.get_groups(include_hidden=include_hidden)
    for g in groups:
        g['member_count'] = len(db.get_group_members(g['ID']))
    return groups


@router.get("/api/groups/{group_id}/members")
def get_group_members(group_id: int):
    db = get_db()
    member_ids = db.get_group_members(group_id)
    employees = db.get_employees(include_hidden=True)
    emp_map = {e['ID']: e for e in employees}
    return [emp_map[mid] for mid in member_ids if mid in emp_map]


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


@router.post("/api/employees", tags=["Employees"], summary="Create employee", description="Create a new employee record. Requires Admin role.")
def create_employee(body: EmployeeCreate, _cur_user: dict = Depends(require_admin)):
    if not body.NAME or not body.NAME.strip():
        raise HTTPException(status_code=400, detail="Feld 'NAME' darf nicht leer sein")
    if body.HRSDAY is not None and body.HRSDAY < 0:
        raise HTTPException(status_code=400, detail="Feld 'HRSDAY' darf nicht negativ sein")
    if body.HRSWEEK is not None and body.HRSWEEK < 0:
        raise HTTPException(status_code=400, detail="Feld 'HRSWEEK' darf nicht negativ sein")
    if body.HRSMONTH is not None and body.HRSMONTH < 0:
        raise HTTPException(status_code=400, detail="Feld 'HRSMONTH' darf nicht negativ sein")
    # Validate optional date fields
    for field_name, val in [('BIRTHDAY', body.BIRTHDAY), ('EMPSTART', body.EMPSTART), ('EMPEND', body.EMPEND)]:
        if val:
            try:
                from datetime import datetime as _dtt
                _dtt.strptime(val, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Feld '{field_name}' muss im Format YYYY-MM-DD sein")
    try:
        result = get_db().create_employee(body.model_dump())
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
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        # Validate negative hours if provided
        for field in ('HRSDAY', 'HRSWEEK', 'HRSMONTH', 'HRSTOTAL'):
            if field in data and data[field] < 0:
                raise HTTPException(status_code=400, detail=f"Feld '{field}' darf nicht negativ sein")
        # Validate date fields if provided
        for field in ('BIRTHDAY', 'EMPSTART', 'EMPEND'):
            if field in data and data[field]:
                try:
                    from datetime import datetime as _dtt
                    _dtt.strptime(data[field], '%Y-%m-%d')
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Feld '{field}' muss im Format YYYY-MM-DD sein")
        result = get_db().update_employee(emp_id, data)
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_employee/{emp_id}')


@router.delete("/api/employees/{emp_id}", tags=["Employees"], summary="Delete (hide) employee", description="Marks an employee as hidden. Requires Admin role.")
def delete_employee(emp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_employee(emp_id)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden")
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
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e, 'create_group')


@router.put("/api/groups/{group_id}")
def update_group(group_id: int, body: GroupUpdate, _cur_user: dict = Depends(require_admin)):
    try:
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        result = get_db().update_group(group_id, data)
        return {"ok": True, "record": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"Gruppe ID {group_id} nicht gefunden")
    except Exception as e:
        raise _sanitize_500(e, f'update_group/{group_id}')


@router.delete("/api/groups/{group_id}")
def delete_group(group_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        count = get_db().delete_group(group_id)
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

from fastapi import UploadFile, File


@router.post("/api/employees/{emp_id}/photo")
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
