"""Recurring shift patterns router (Q066).

Allows defining weekly/biweekly shift patterns per employee or group,
then generating concrete schedule entries for a date range.
"""

import json
import os
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..dependencies import _logger, get_db, require_planer

router = APIRouter()

# ── Storage ───────────────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_RECURRING_FILE = os.path.join(_DATA_DIR, "recurring_shifts.json")
_LOCK = threading.Lock()


def _read_all() -> list[dict]:
    """Read all recurring shift patterns."""
    try:
        with open(_RECURRING_FILE) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_all(data: list[dict]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_RECURRING_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Schemas ───────────────────────────────────────────────────────────────────

_TIME_PATTERN = r"^\d{2}:\d{2}$"
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class RecurringShiftCreate(BaseModel):
    """Request body for creating a recurring shift pattern."""

    employee_id: int | None = Field(None, description="Employee ID (or None for group-wide)")
    group_id: int | None = Field(None, description="Group ID (or None for individual)")
    shift_type: int = Field(..., description="Shift ID (integer)")
    start_time: str = Field(..., pattern=_TIME_PATTERN, description="Shift start time HH:MM")
    end_time: str = Field(..., pattern=_TIME_PATTERN, description="Shift end time HH:MM")
    recurrence: Literal["weekly", "biweekly"] = Field(..., description="Recurrence pattern")
    day_of_week: int = Field(..., ge=0, le=6, description="Day of week: 0=Monday, 6=Sunday")
    valid_from: str = Field(..., pattern=_DATE_PATTERN, description="Pattern valid from YYYY-MM-DD")
    valid_until: str | None = Field(None, pattern=_DATE_PATTERN, description="Pattern valid until YYYY-MM-DD (null = indefinite)")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        h, m = int(v[:2]), int(v[3:])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid time: {v}")
        return v

    @field_validator("valid_from", "valid_until")
    @classmethod
    def validate_date(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid date: {v}")
        return v

    def model_post_init(self, __context) -> None:
        if self.employee_id is None and self.group_id is None:
            raise ValueError("Either employee_id or group_id must be provided")
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until must be >= valid_from")


class GenerateRequest(BaseModel):
    """Request body for generating concrete shifts from a pattern."""

    from_date: str = Field(..., pattern=_DATE_PATTERN, description="Generate from YYYY-MM-DD")
    to_date: str = Field(..., pattern=_DATE_PATTERN, description="Generate to YYYY-MM-DD (inclusive)")

    @field_validator("from_date", "to_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid date: {v}")
        return v

    def model_post_init(self, __context) -> None:
        if self.to_date < self.from_date:
            raise ValueError("to_date must be >= from_date")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/api/shifts/recurring",
    tags=["Recurring Shifts"],
    summary="Create recurring shift pattern",
    description="Create a weekly or biweekly recurring shift pattern for an employee or group.",
)
def create_recurring_shift(
    body: RecurringShiftCreate,
    _cur_user: dict = Depends(require_planer),
):
    db = get_db()

    # Validate employee if given
    if body.employee_id is not None:
        emp = db.get_employee(body.employee_id)
        if emp is None:
            raise HTTPException(
                status_code=404,
                detail=f"Mitarbeiter {body.employee_id} nicht gefunden",
            )

    # Validate shift type exists
    shift = db.get_shift(body.shift_type)
    if shift is None:
        raise HTTPException(
            status_code=404,
            detail=f"Schicht {body.shift_type} nicht gefunden",
        )

    record = {
        "id": str(uuid.uuid4()),
        "employee_id": body.employee_id,
        "group_id": body.group_id,
        "shift_type": body.shift_type,
        "start_time": body.start_time,
        "end_time": body.end_time,
        "recurrence": body.recurrence,
        "day_of_week": body.day_of_week,
        "valid_from": body.valid_from,
        "valid_until": body.valid_until,
        "created_at": datetime.now(UTC).isoformat() + "Z",
    }

    with _LOCK:
        patterns = _read_all()
        patterns.append(record)
        _write_all(patterns)

    _logger.info("RecurringShift created: %s", record["id"])
    return {"ok": True, "pattern": record}


@router.get(
    "/api/shifts/recurring",
    tags=["Recurring Shifts"],
    summary="List recurring shift patterns",
    description="Return all recurring shift patterns, optionally filtered by employee_id or group_id.",
)
def list_recurring_shifts(
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    group_id: int | None = Query(None, description="Filter by group ID"),
):
    patterns = _read_all()

    if employee_id is not None:
        patterns = [p for p in patterns if p.get("employee_id") == employee_id]
    if group_id is not None:
        patterns = [p for p in patterns if p.get("group_id") == group_id]

    return {"patterns": patterns, "total": len(patterns)}


@router.delete(
    "/api/shifts/recurring/{pattern_id}",
    tags=["Recurring Shifts"],
    summary="Delete recurring shift pattern",
    description="Delete a recurring shift pattern by ID. Requires Planer role.",
)
def delete_recurring_shift(
    pattern_id: str,
    _cur_user: dict = Depends(require_planer),
):
    with _LOCK:
        patterns = _read_all()
        remaining = [p for p in patterns if p.get("id") != pattern_id]
        if len(remaining) == len(patterns):
            raise HTTPException(
                status_code=404,
                detail=f"Muster {pattern_id} nicht gefunden",
            )
        _write_all(remaining)

    _logger.info("RecurringShift deleted: %s", pattern_id)
    return {"ok": True, "deleted_id": pattern_id}


@router.post(
    "/api/shifts/recurring/{pattern_id}/generate",
    tags=["Recurring Shifts"],
    summary="Generate concrete shifts from pattern",
    description=(
        "Generate concrete schedule entries for a date range from a recurring pattern. "
        "Skips dates where the employee already has the shift assigned. "
        "Returns {generated, skipped}."
    ),
)
def generate_shifts(
    pattern_id: str,
    body: GenerateRequest,
    _cur_user: dict = Depends(require_planer),
):
    # Find pattern
    patterns = _read_all()
    pattern = next((p for p in patterns if p.get("id") == pattern_id), None)
    if pattern is None:
        raise HTTPException(
            status_code=404,
            detail=f"Muster {pattern_id} nicht gefunden",
        )

    db = get_db()
    from_dt = date.fromisoformat(body.from_date)
    to_dt = date.fromisoformat(body.to_date)

    # Clamp to pattern validity
    valid_from = date.fromisoformat(pattern["valid_from"])
    valid_until = date.fromisoformat(pattern["valid_until"]) if pattern.get("valid_until") else None

    effective_from = max(from_dt, valid_from)
    effective_to = to_dt
    if valid_until is not None:
        effective_to = min(to_dt, valid_until)

    if effective_from > effective_to:
        return {"generated": 0, "skipped": 0, "dates": []}

    target_dow = pattern["day_of_week"]  # 0=Monday, 6=Sunday
    recurrence = pattern["recurrence"]
    shift_type = pattern["shift_type"]
    employee_id = pattern.get("employee_id")

    if employee_id is None:
        raise HTTPException(
            status_code=422,
            detail="Dieses Muster hat keine employee_id — Gruppen-Generierung noch nicht unterstützt",
        )

    # Collect all matching dates in the range
    candidate_dates: list[date] = []
    current = effective_from

    # Find the first occurrence of target_dow >= current
    days_ahead = (target_dow - current.weekday()) % 7
    first_occurrence = current + timedelta(days=days_ahead)

    # Step size for recurrence
    step = 7 if recurrence == "weekly" else 14

    d = first_occurrence
    while d <= effective_to:
        candidate_dates.append(d)
        d += timedelta(days=step)

    # Get existing MASHI entries for this employee in the range (best-effort)
    existing_dates: set[str] = set()
    try:
        from sp5lib.dbf_reader import get_table_fields
        from sp5lib.dbf_writer import find_all_records

        filepath = db._table("MASHI")
        fields = get_table_fields(filepath)
        # Load all entries for this employee — filter by date range
        all_entries = find_all_records(filepath, fields, EMPLOYEEID=employee_id)
        for _, rec in all_entries:
            rec_date = rec.get("DATE", "")
            if rec_date and rec.get("SHIFTID") == shift_type:
                existing_dates.add(rec_date)
    except Exception:
        pass  # best-effort, proceed with generation

    generated = 0
    skipped = 0
    generated_dates: list[str] = []

    for d in candidate_dates:
        date_str = d.isoformat()
        if date_str in existing_dates:
            skipped += 1
            continue
        try:
            db.add_schedule_entry(employee_id, date_str, shift_type)
            generated += 1
            generated_dates.append(date_str)
            existing_dates.add(date_str)  # prevent duplicates within same call
        except Exception as exc:
            _logger.warning(
                "RecurringShift generate: skip %s for emp %s on %s — %s",
                pattern_id,
                employee_id,
                date_str,
                exc,
            )
            skipped += 1

    _logger.info(
        "RecurringShift generate %s: generated=%d skipped=%d",
        pattern_id,
        generated,
        skipped,
    )
    return {"generated": generated, "skipped": skipped, "dates": generated_dates}
