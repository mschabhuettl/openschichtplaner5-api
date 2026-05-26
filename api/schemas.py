"""Pydantic response schemas for OpenAPI documentation.

These models describe the shape of GET responses for key resources.
They use extra='allow' so additional DBF fields pass through without error.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict


class PaginatedResponse[T](BaseModel):
    """Generic paginated response wrapper."""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


def paginate(
    data: Sequence,
    page: int | None = None,
    page_size: int = 50,
) -> dict | list:
    """Return paginated dict if page is set, else return plain list.

    Backward-compatible: omitting ``page`` returns the raw list so
    existing callers keep working.
    """
    if page is None:
        return list(data)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    total = len(data)
    pages = math.ceil(total / page_size) if total else 1
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": list(data[start:end]),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


class _FlexModel(BaseModel):
    """Base model that allows extra fields (DBF tables may include additional columns)."""

    model_config = ConfigDict(extra="allow")


# ── Employee ──────────────────────────────────────────────────────────────────


class EmployeeResponse(_FlexModel):
    # Field names mirror the real DBF/SP5Database keys (extra columns pass through
    # via _FlexModel). Previously this declared HIDDEN/EMPLOYEENO/GROUPID/WORKPLACEID/
    # CONTRACTHOURS, none of which exist on the payload — they only surfaced as
    # misleading always-null fields in the OpenAPI schema.
    ID: int
    NAME: str
    FIRSTNAME: str | None = None
    SHORTNAME: str | None = None
    BIRTHDAY: str | None = None
    HIDE: bool | None = None
    NUMBER: str | None = None
    POSITION: int | None = None
    FUNCTION: str | None = None
    EMAIL: str | None = None
    PHONE: str | None = None


# ── Group ─────────────────────────────────────────────────────────────────────


class GroupResponse(_FlexModel):
    ID: int
    NAME: str
    SHORTNAME: str | None = None
    HIDE: bool | None = None  # real DBF key (was HIDDEN, which was always null)
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
