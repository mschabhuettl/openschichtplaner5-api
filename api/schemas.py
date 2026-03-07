"""Pydantic response schemas for OpenAPI documentation.

These models describe the shape of GET responses for key resources.
They use extra='allow' so additional DBF fields pass through without error.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _FlexModel(BaseModel):
    """Base model that allows extra fields (DBF tables may include additional columns)."""

    model_config = ConfigDict(extra="allow")


# ── Employee ──────────────────────────────────────────────────────────────────


class EmployeeResponse(_FlexModel):
    ID: int
    NAME: str
    FIRSTNAME: str | None = None
    SHORTNAME: str | None = None
    BIRTHDAY: str | None = None
    HIDDEN: bool | None = None
    EMPLOYEENO: str | None = None
    GROUPID: int | None = None
    WORKPLACEID: int | None = None
    CONTRACTHOURS: float | None = None


# ── Group ─────────────────────────────────────────────────────────────────────


class GroupResponse(_FlexModel):
    ID: int
    NAME: str
    SHORTNAME: str | None = None
    HIDDEN: bool | None = None
    member_count: int | None = None


# ── Shift ─────────────────────────────────────────────────────────────────────


class ShiftResponse(_FlexModel):
    ID: int
    NAME: str
    SHORTNAME: str | None = None
    COLORBK: int | None = None
    COLORTEXT: int | None = None
    HIDDEN: bool | None = None
    STARTEND0: str | None = None


# ── Absence ──────────────────────────────────────────────────────────────────


class AbsenceResponse(_FlexModel):
    ID: int
    EMPLOYEEID: int
    DATE: str
    LEAVETYPID: int | None = None
    STATUS: str | None = None
    NOTE: str | None = None


# ── Schedule entry ────────────────────────────────────────────────────────────


class ScheduleEntryResponse(_FlexModel):
    employee_id: int
    employee_name: str
    employee_short: str | None = None
    date: str
    kind: str
    shift_id: int | None = None
    shift_name: str | None = None
    shift_short: str | None = None
    color_bk: str | None = None
    color_text: str | None = None
