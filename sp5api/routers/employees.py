"""Employees and groups router."""

import csv
import io
import os
import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import cache
from ..dependencies import (
    _logger,
    _sanitize_500,
    get_db,
    require_admin,
)
from ..schemas import GroupResponse, paginate
from .events import broadcast

router = APIRouter()


@router.get(
    "/api/employees",
    tags=["Employees"],
    summary="List employees",
    description="Return all active employees. Set include_hidden=true to include hidden/archived employees. "
    "Pass page & page_size for paginated results.",
)
def get_employees(
    include_hidden: bool = False,
    page: int | None = Query(None, ge=1, description="Page number (1-based). Omit for unpaginated list."),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
):
    cache_key = f"employees:list:{include_hidden}"
    cached = cache.get(cache_key)
    if cached is not None:
        return paginate(cached, page, page_size)
    result = get_db().get_employees(include_hidden=include_hidden)
    cache.put(cache_key, result)
    return paginate(result, page, page_size)


@router.get("/api/employees/{emp_id}", tags=["Employees"], summary="Get employee by ID", description="Return a single employee record by ID.")
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
    cache_key = f"groups:list:{include_hidden}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    db = get_db()
    groups = db.get_groups(include_hidden=include_hidden)
    # Fetch all group→members in a single pass to avoid N+1
    all_members = db.get_all_group_members()
    for g in groups:
        g["member_count"] = len(all_members.get(g["ID"], []))
    cache.put(cache_key, groups)
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
            raise ValueError(f"'{field_name}' is not a valid date")
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
        return v.strip()

    @field_validator("EMAIL", mode="before")
    @classmethod
    def validate_email(cls, v):
        import re as _re
        if v and not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(v)):
            raise ValueError("Invalid email address")
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
        return v.strip() if v is not None else v

    @field_validator("EMAIL", mode="before")
    @classmethod
    def validate_email(cls, v):
        import re as _re
        if v and not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(v)):
            raise ValueError("Invalid email address")
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
        cache.invalidate("employees:")
        broadcast("employee_changed", {"action": "created", "employee_id": result.get("ID")})
        return {"ok": True, "record": result}
    except ValueError as e:
        if str(e).startswith("DUPLICATE:SHORTNAME:"):
            sn = (body.SHORTNAME or "").strip()
            raise HTTPException(
                status_code=409, detail=f"Abbreviation '{sn}' is already taken"
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
        cache.invalidate("employees:")
        broadcast("employee_changed", {"action": "updated", "employee_id": emp_id})
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
    summary="Deactivate (soft-delete) employee",
    description="Marks an employee as inactive/hidden. Historical data (shifts, absences) is preserved. Requires Admin role.",
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
            "AUDIT EMPLOYEE_DEACTIVATE | user=%s emp_id=%d", _cur_user.get("NAME"), emp_id
        )
        # Audit: employee deactivated
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="DEACTIVATE",
            entity="employee",
            entity_id=emp_id,
            details=f"Mitarbeiter {old_name} (ID {emp_id}) deaktiviert",
            old_value={"NAME": old_name, "HIDE": False},
            new_value={"HIDE": True},
            user_id=_cur_user.get("ID"),
        )
        cache.invalidate("employees:")
        broadcast("employee_changed", {"action": "deactivated", "employee_id": emp_id})
        return {"ok": True, "deactivated": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f"delete_employee/{emp_id}")


@router.put(
    "/api/employees/{emp_id}/activate",
    tags=["Employees"],
    summary="Reactivate employee",
    description="Reactivates a previously deactivated (hidden) employee. Requires Admin role.",
)
def activate_employee(emp_id: int, _cur_user: dict = Depends(require_admin)):
    try:
        db = get_db()
        old_emp = db.get_employee(emp_id)
        if old_emp is None:
            raise HTTPException(
                status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
            )
        old_name = old_emp.get("NAME", "?")
        count = db.activate_employee(emp_id)
        if count == 0:
            raise HTTPException(
                status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
            )
        _logger.warning(
            "AUDIT EMPLOYEE_ACTIVATE | user=%s emp_id=%d", _cur_user.get("NAME"), emp_id
        )
        db.log_action(
            user=_cur_user.get("NAME", "?"),
            action="ACTIVATE",
            entity="employee",
            entity_id=emp_id,
            details=f"Mitarbeiter {old_name} (ID {emp_id}) reaktiviert",
            old_value={"HIDE": True},
            new_value={"HIDE": False},
            user_id=_cur_user.get("ID"),
        )
        cache.invalidate("employees:")
        broadcast("employee_changed", {"action": "activated", "employee_id": emp_id})
        return {"ok": True, "activated": count}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, f"activate_employee/{emp_id}")


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
    # Prefer WebP, then fall back to other formats
    for ext in (".webp", ".jpg", ".jpeg", ".png", ".gif"):
        p = photos_dir / f"{emp_id}{ext}"
        if p.exists():
            media_type = {
                ".webp": "image/webp",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
            }.get(ext, "application/octet-stream")
            return _FileResponse(str(p), media_type=media_type)
    raise HTTPException(status_code=404, detail="Kein Foto vorhanden")


# ── Write: Groups ─────────────────────────────────────────────


class GroupCreate(BaseModel):
    NAME: str = Field(..., min_length=1, max_length=100)
    SHORTNAME: str = Field("", max_length=20)
    SUPERID: int = 0
    HIDE: bool = False
    BOLD: int = Field(0, ge=0, le=1)
    DAILYDEM: int = Field(0, ge=0)
    ARBITR: str = Field("", max_length=200)
    CFGLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKSCHED: int | None = Field(None, ge=0, le=16777215)


class GroupUpdate(BaseModel):
    NAME: str | None = Field(None, min_length=1, max_length=100)
    SHORTNAME: str | None = Field(None, max_length=20)
    SUPERID: int | None = None
    POSITION: int | None = None
    HIDE: bool | None = None
    BOLD: int | None = Field(None, ge=0, le=1)
    DAILYDEM: int | None = Field(None, ge=0)
    ARBITR: str | None = Field(None, max_length=200)
    CFGLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKLABEL: int | None = Field(None, ge=0, le=16777215)
    CBKSCHED: int | None = Field(None, ge=0, le=16777215)

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
        cache.invalidate("groups:")
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
        cache.invalidate("groups:")
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
        cache.invalidate("groups:")
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
        cache.invalidate("groups:")
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
        cache.invalidate("groups:")
        return {"ok": True, "removed": count}
    except Exception as e:
        raise _sanitize_500(e, f"remove_group_member/{group_id}/{emp_id}")


# ── Import endpoints ─────────────────────────────────────────


@router.post(
    "/api/employees/{emp_id}/photo", tags=["Employees"], summary="Upload employee photo"
)
async def upload_employee_photo(
    emp_id: int,
    file: UploadFile = File(...),
    crop_x: int = Query(0, ge=0, description="Crop area X offset in pixels"),
    crop_y: int = Query(0, ge=0, description="Crop area Y offset in pixels"),
    crop_w: int = Query(0, ge=0, description="Crop area width in pixels (0 = full)"),
    crop_h: int = Query(0, ge=0, description="Crop area height in pixels (0 = full)"),
    _cur_user: dict = Depends(require_admin),
):
    """Upload a photo for an employee.

    Accepts JPG/PNG/GIF/WebP.  The image is optionally cropped (if crop_w/crop_h > 0),
    resized to fit within 400×400 px, and stored as WebP.
    """
    import io as _io
    import pathlib

    from PIL import Image

    photos_dir = pathlib.Path(_PHOTOS_DIR)
    photos_dir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter {emp_id} nicht gefunden"
        )

    ct = (file.content_type or "").lower()
    allowed = ("image/jpeg", "image/png", "image/gif", "image/webp")
    if ct not in allowed:
        raise HTTPException(status_code=400, detail="Nur JPG, PNG, GIF oder WebP erlaubt")

    content = await file.read()
    _MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB
    if len(content) > _MAX_PHOTO_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max. 5 MB)")

    # Open image with Pillow
    try:
        # Annotate as Image.Image: Image.open() returns an ImageFile, but convert()/
        # crop() below return a plain Image — without this the reassignments are
        # flagged as incompatible by the type checker.
        img: Image.Image = Image.open(_io.BytesIO(content))
        img = img.convert("RGB")  # ensure RGB for WebP
    except Exception:
        raise HTTPException(status_code=400, detail="Bild konnte nicht gelesen werden")

    # Apply crop if specified
    if crop_w > 0 and crop_h > 0:
        img_w, img_h = img.size
        # Clamp crop to image bounds
        cx = min(crop_x, img_w)
        cy = min(crop_y, img_h)
        cw = min(crop_w, img_w - cx)
        ch = min(crop_h, img_h - cy)
        if cw > 0 and ch > 0:
            img = img.crop((cx, cy, cx + cw, cy + ch))

    # Resize to max 400x400 preserving aspect ratio
    max_size = (400, 400)
    # Image.Resampling.LANCZOS is the supported form; the top-level Image.LANCZOS
    # alias is deprecated and slated for removal in a future Pillow.
    img.thumbnail(max_size, Image.Resampling.LANCZOS)

    # Remove old photos for this employee
    for old in photos_dir.glob(f"{emp_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass

    # Save as WebP
    dest = photos_dir / f"{emp_id}.webp"
    buf = _io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    dest.write_bytes(buf.getvalue())

    rel_path = f"uploads/photos/{emp_id}.webp"
    try:
        db.update_employee(emp_id, {"PHOTO": rel_path})
    except Exception:
        pass  # best effort

    return {"ok": True, "photo_url": f"/api/employees/{emp_id}/photo", "path": rel_path}


# ── CSV Import ────────────────────────────────────────────────

_CSV_REQUIRED_COLUMNS = {"first_name", "last_name"}
_CSV_OPTIONAL_COLUMNS = {"email", "phone", "group_id", "contract_hours", "qualifications"}
_CSV_ALL_COLUMNS = _CSV_REQUIRED_COLUMNS | _CSV_OPTIONAL_COLUMNS
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post(
    "/api/employees/import-csv",
    tags=["Import"],
    summary="Import employees from CSV",
    description=(
        "Bulk-create employees from a CSV file upload. "
        "Required columns: first_name, last_name. "
        "Optional: email, phone, group_id, contract_hours, qualifications (comma-separated). "
        "Skips duplicates (same first_name + last_name). "
        "Rolls back all if >50% of rows have errors. Admin-only."
    ),
)
async def import_employees_csv(
    file: UploadFile = File(...),
    _cur_user: dict = Depends(require_admin),
):
    """Import employees from a CSV file."""
    # Validate content type
    ct = (file.content_type or "").lower()
    if ct and ct not in ("text/csv", "application/octet-stream", "text/plain"):
        raise HTTPException(status_code=400, detail="Nur CSV-Dateien erlaubt")

    # Read and decode file
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Datei zu groß (max. 5 MB)")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="CSV-Datei ist leer")

    try:
        text = raw.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV-Datei konnte nicht dekodiert werden")

    # Parse CSV
    # Try to detect delimiter
    sniffer_sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sniffer_sample, delimiters=",;\t")
    except csv.Error:
        dialect = None

    reader = csv.DictReader(io.StringIO(text), dialect=dialect) if dialect else csv.DictReader(io.StringIO(text))

    # Normalize header names
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV-Header fehlt")

    # Map headers: strip whitespace, lowercase
    header_map = {}
    for h in reader.fieldnames:
        normalized = h.strip().lower().replace(" ", "_")
        header_map[h] = normalized

    # Check required columns exist
    normalized_headers = set(header_map.values())
    missing = _CSV_REQUIRED_COLUMNS - normalized_headers
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Pflicht-Spalten fehlen: {', '.join(sorted(missing))}",
        )

    # Load existing employees for duplicate check
    db = get_db()
    existing_employees = db.get_employees(include_hidden=True)
    existing_names = {
        (e.get("FIRSTNAME", "").strip().lower(), e.get("NAME", "").strip().lower())
        for e in existing_employees
    }

    # Load existing groups for validation
    existing_groups = db.get_groups(include_hidden=True)
    valid_group_ids = {g["ID"] for g in existing_groups}

    # Process rows
    rows_to_create: list[dict] = []
    errors: list[dict] = []
    skipped = 0
    total_rows = 0

    for row_num, raw_row in enumerate(reader, start=2):  # row 1 is header
        total_rows += 1
        # Normalize keys
        row = {header_map.get(k, k): (v.strip() if v else "") for k, v in raw_row.items()}

        first_name = row.get("first_name", "").strip()
        last_name = row.get("last_name", "").strip()

        # Validate required fields
        row_errors = []
        if not first_name:
            row_errors.append({"row": row_num, "field": "first_name", "message": "Vorname ist Pflichtfeld"})
        if not last_name:
            row_errors.append({"row": row_num, "field": "last_name", "message": "Nachname ist Pflichtfeld"})

        if row_errors:
            errors.extend(row_errors)
            continue

        # Check duplicate
        name_key = (first_name.lower(), last_name.lower())
        if name_key in existing_names:
            skipped += 1
            continue

        # Validate optional fields
        email = row.get("email", "").strip()
        if email and not _EMAIL_RE.match(email):
            errors.append({"row": row_num, "field": "email", "message": "Ungültige E-Mail-Adresse"})
            continue

        phone = row.get("phone", "").strip()

        group_id_str = row.get("group_id", "").strip()
        group_id = None
        if group_id_str:
            try:
                group_id = int(group_id_str)
                if group_id not in valid_group_ids:
                    errors.append({"row": row_num, "field": "group_id", "message": f"Gruppe {group_id} existiert nicht"})
                    continue
            except ValueError:
                errors.append({"row": row_num, "field": "group_id", "message": "group_id muss eine Zahl sein"})
                continue

        contract_hours_str = row.get("contract_hours", "").strip()
        contract_hours = 0.0
        if contract_hours_str:
            try:
                contract_hours = float(contract_hours_str)
                if contract_hours < 0 or contract_hours > 168:
                    errors.append({"row": row_num, "field": "contract_hours", "message": "contract_hours muss zwischen 0 und 168 liegen"})
                    continue
            except ValueError:
                errors.append({"row": row_num, "field": "contract_hours", "message": "contract_hours muss eine Zahl sein"})
                continue

        qualifications = row.get("qualifications", "").strip()

        # Add to existing_names to catch duplicates within CSV
        existing_names.add(name_key)

        rows_to_create.append({
            "NAME": last_name,
            "FIRSTNAME": first_name,
            "EMAIL": email,
            "PHONE": phone,
            "HRSWEEK": contract_hours,
            "NOTE1": qualifications,  # Store qualifications in NOTE1
            "_group_id": group_id,
        })

    if total_rows == 0:
        raise HTTPException(status_code=400, detail="CSV-Datei enthält keine Datenzeilen")

    # Check error threshold: if >50% errors, rollback all
    valid_rows = len(rows_to_create)
    error_rows = len(errors)
    processable = valid_rows + error_rows  # excludes skipped
    if processable > 0 and error_rows / processable > 0.5:
        return {
            "created": 0,
            "skipped": skipped,
            "errors": errors,
            "rolled_back": True,
            "message": f"Zu viele Fehler ({error_rows}/{processable} Zeilen). Import abgebrochen.",
        }

    # Create employees
    created = 0
    for emp_data in rows_to_create:
        group_id = emp_data.pop("_group_id", None)
        try:
            result = db.create_employee(emp_data)
            created += 1
            emp_id = result.get("ID")

            # Assign to group if specified
            if group_id is not None and emp_id:
                try:
                    db.add_group_member(group_id, emp_id)
                except Exception as ge:
                    _logger.warning("CSV import: group assignment failed for emp %s: %s", emp_id, ge)

            _logger.info(
                "CSV_IMPORT employee created | user=%s name=%s %s id=%s",
                _cur_user.get("NAME"),
                emp_data.get("NAME"),
                emp_data.get("FIRSTNAME"),
                emp_id,
            )
        except Exception as e:
            _logger.warning("CSV import: create_employee failed: %s", e)
            errors.append({
                "row": 0,
                "field": "create",
                "message": f"Fehler beim Erstellen: {emp_data.get('FIRSTNAME')} {emp_data.get('NAME')}",
            })

    cache.invalidate("employees:", "groups:")
    if created > 0:
        broadcast("employee_changed", {"action": "csv_import", "count": created})

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


# ── Bulk Operations ─────────────────────────────────────────


class BulkEmployeeAction(BaseModel):
    employee_ids: list[int] = Field(
        ..., min_length=1, max_length=500, description="Liste von Mitarbeiter-IDs"
    )
    action: str = Field(
        ...,
        pattern=r"^(hide|show|assign_group|remove_group)$",
        description="'hide', 'show', 'assign_group', 'remove_group'",
    )
    group_id: int | None = Field(
        None, gt=0, description="Target group for assign_group/remove_group"
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
                status_code=400, detail="group_id required for assign_group"
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
                status_code=400, detail="group_id required for remove_group"
            )
        for emp_id in body.employee_ids:
            try:
                db.remove_group_member(body.group_id, emp_id)
                affected += 1
            except Exception as e:
                errors.append({"id": emp_id, "error": str(e)})

    else:
        raise HTTPException(status_code=400, detail=f"Unbekannte Aktion: {body.action}")

    cache.invalidate("employees:", "groups:")
    return {"ok": True, "affected": affected, "errors": errors}
