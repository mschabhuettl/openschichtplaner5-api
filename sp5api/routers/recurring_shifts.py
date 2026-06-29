"""Recurring shift patterns router (Q066).

Allows defining weekly/biweekly shift patterns per employee, then generating
concrete schedule entries for a date range. A pattern only references a shift
(by ``shift_id``); the shift definition itself carries the start/end times, so
the pattern does not duplicate them.

The list and create responses are enriched with ``employee_name`` /
``shift_name`` / ``shift_short`` so the frontend can render rows directly.
"""

import json
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .._paths import state_path
from ..dependencies import _logger, get_db, require_planer

router = APIRouter()

# ── Storage ───────────────────────────────────────────────────────────────────

_RECURRING_FILE = state_path("recurring_shifts.json")
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
    with open(_RECURRING_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _next_id(patterns: list[dict]) -> int:
    """Allocate the next integer pattern ID (max+1, ignoring non-int ids)."""
    ids = [p["id"] for p in patterns if isinstance(p.get("id"), int)]
    return (max(ids) + 1) if ids else 1


def _enrich(db, rec: dict) -> dict:
    """Project a stored pattern to the API/frontend shape with display names."""
    emp = db.get_employee(rec["employee_id"])
    shift = db.get_shift(rec["shift_id"])
    if emp is not None:
        employee_name = f"{emp.get('FIRSTNAME', '')} {emp.get('NAME', '')}".strip()
    else:
        employee_name = f"#{rec['employee_id']}"
    return {
        "id": rec["id"],
        "employee_id": rec["employee_id"],
        "employee_name": employee_name or f"#{rec['employee_id']}",
        "shift_id": rec["shift_id"],
        "shift_name": shift.get("NAME", "") if shift else f"#{rec['shift_id']}",
        "shift_short": shift.get("SHORTNAME", "") if shift else "",
        "recurrence": rec["recurrence"],
        "day_of_week": rec["day_of_week"],
        "valid_from": rec["valid_from"],
        "valid_until": rec.get("valid_until"),
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class RecurringShiftCreate(BaseModel):
    """Request body for creating a recurring shift pattern."""

    employee_id: int = Field(..., description="Employee ID")
    shift_id: int = Field(..., description="Shift ID")
    recurrence: Literal["weekly", "biweekly"] = Field(..., description="Recurrence pattern")
    day_of_week: int = Field(..., ge=0, le=6, description="Day of week: 0=Monday, 6=Sunday")
    valid_from: str = Field(..., pattern=_DATE_PATTERN, description="Pattern valid from YYYY-MM-DD")
    valid_until: str | None = Field(
        None, pattern=_DATE_PATTERN, description="Pattern valid until YYYY-MM-DD (null = indefinite)"
    )

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
    description="Create a weekly or biweekly recurring shift pattern for an employee.",
)
def create_recurring_shift(
    body: RecurringShiftCreate,
    _cur_user: dict = Depends(require_planer),
):
    db = get_db()

    emp = db.get_employee(body.employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter {body.employee_id} nicht gefunden")

    shift = db.get_shift(body.shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail=f"Schicht {body.shift_id} nicht gefunden")

    with _LOCK:
        patterns = _read_all()
        record = {
            "id": _next_id(patterns),
            "employee_id": body.employee_id,
            "shift_id": body.shift_id,
            "recurrence": body.recurrence,
            "day_of_week": body.day_of_week,
            "valid_from": body.valid_from,
            "valid_until": body.valid_until,
            "created_at": datetime.now(UTC).isoformat() + "Z",
        }
        patterns.append(record)
        _write_all(patterns)

    _logger.info("RecurringShift created: %s", record["id"])
    return _enrich(db, record)


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
    db = get_db()
    patterns = _read_all()

    if employee_id is not None:
        patterns = [p for p in patterns if p.get("employee_id") == employee_id]
    if group_id is not None:
        member_ids = set(db.get_group_members(group_id))
        patterns = [p for p in patterns if p.get("employee_id") in member_ids]

    return [_enrich(db, p) for p in patterns]


@router.delete(
    "/api/shifts/recurring/{pattern_id}",
    tags=["Recurring Shifts"],
    summary="Delete recurring shift pattern",
    description="Delete a recurring shift pattern by ID. Requires Planer role.",
)
def delete_recurring_shift(
    pattern_id: int,
    _cur_user: dict = Depends(require_planer),
):
    with _LOCK:
        patterns = _read_all()
        remaining = [p for p in patterns if p.get("id") != pattern_id]
        if len(remaining) == len(patterns):
            raise HTTPException(status_code=404, detail=f"Muster {pattern_id} nicht gefunden")
        _write_all(remaining)

    _logger.info("RecurringShift deleted: %s", pattern_id)
    return {"ok": True, "deleted": pattern_id}


@router.post(
    "/api/shifts/recurring/{pattern_id}/generate",
    tags=["Recurring Shifts"],
    summary="Generate concrete shifts from pattern",
    description=(
        "Generate concrete schedule entries for a date range from a recurring pattern. "
        "Skips dates where the employee already has the shift assigned. "
        "Returns {created, skipped}."
    ),
)
def generate_shifts(
    pattern_id: int,
    body: GenerateRequest,
    _cur_user: dict = Depends(require_planer),
):
    patterns = _read_all()
    pattern = next((p for p in patterns if p.get("id") == pattern_id), None)
    if pattern is None:
        raise HTTPException(status_code=404, detail=f"Muster {pattern_id} nicht gefunden")

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
        return {"created": 0, "skipped": 0}

    target_dow = pattern["day_of_week"]  # 0=Monday, 6=Sunday
    recurrence = pattern["recurrence"]
    shift_id = pattern["shift_id"]
    employee_id = pattern["employee_id"]

    # Collect all matching dates in the range
    days_ahead = (target_dow - effective_from.weekday()) % 7
    first_occurrence = effective_from + timedelta(days=days_ahead)
    step = 7 if recurrence == "weekly" else 14

    candidate_dates: list[date] = []
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
        all_entries = find_all_records(filepath, fields, EMPLOYEEID=employee_id)
        for _, rec in all_entries:
            rec_date = rec.get("DATE", "")
            if rec_date and rec.get("SHIFTID") == shift_id:
                existing_dates.add(rec_date)
    except Exception:
        pass  # best-effort, proceed with generation

    created = 0
    skipped = 0

    for d in candidate_dates:
        date_str = d.isoformat()
        if date_str in existing_dates:
            skipped += 1
            continue
        try:
            db.add_schedule_entry(employee_id, date_str, shift_id)
            created += 1
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

    _logger.info("RecurringShift generate %s: created=%d skipped=%d", pattern_id, created, skipped)
    return {"created": created, "skipped": skipped}
