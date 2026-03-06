"""Pydantic response schemas for OpenAPI documentation.

These models describe the shape of GET responses for key resources.
They use extra='allow' so additional DBF fields pass through without error.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, ConfigDict


class _FlexModel(BaseModel):
    """Base model that allows extra fields (DBF tables may include additional columns)."""
    model_config = ConfigDict(extra="allow")


# ── Employee ──────────────────────────────────────────────────────────────────

class EmployeeResponse(_FlexModel):
    ID: int
    NAME: str
    FIRSTNAME: Optional[str] = None
    SHORTNAME: Optional[str] = None
    BIRTHDAY: Optional[str] = None
    HIDDEN: Optional[bool] = None
    EMPLOYEENO: Optional[str] = None
    GROUPID: Optional[int] = None
    WORKPLACEID: Optional[int] = None
    CONTRACTHOURS: Optional[float] = None


# ── Group ─────────────────────────────────────────────────────────────────────

class GroupResponse(_FlexModel):
    ID: int
    NAME: str
    SHORTNAME: Optional[str] = None
    HIDDEN: Optional[bool] = None
    member_count: Optional[int] = None


# ── Shift ─────────────────────────────────────────────────────────────────────

class ShiftResponse(_FlexModel):
    ID: int
    NAME: str
    SHORTNAME: Optional[str] = None
    COLORBK: Optional[int] = None
    COLORTEXT: Optional[int] = None
    HIDDEN: Optional[bool] = None
    STARTEND0: Optional[str] = None


# ── Absence ──────────────────────────────────────────────────────────────────

class AbsenceResponse(_FlexModel):
    ID: int
    EMPLOYEEID: int
    DATE: str
    LEAVETYPID: Optional[int] = None
    STATUS: Optional[str] = None
    NOTE: Optional[str] = None


# ── Schedule entry ────────────────────────────────────────────────────────────

class ScheduleEntryResponse(_FlexModel):
    employee_id: int
    employee_name: str
    employee_short: Optional[str] = None
    date: str
    kind: str
    shift_id: Optional[int] = None
    shift_name: Optional[str] = None
    shift_short: Optional[str] = None
    color_bk: Optional[str] = None
    color_text: Optional[str] = None
